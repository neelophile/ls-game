from discord import app_commands, Interaction, Embed, Color, Forbidden, Member, TextStyle, SelectOption
from discord.ext import commands
from discord.ui import Modal, TextInput, View
from db.database import get_session
from db.models import Game, Player, TrustMatrix, Information, GameStatus, Phase, Role, InformationType, Turn, Vote
from random import choice, shuffle, sample
from cogs.justice import VoteView
from cogs.resolution import InvestigateSelectView, KiraJudgmentView


def generate_investigator_info(players: list[Player], investigator: Player):
    l_player = next(i for i in players if i.role == Role.l)
    worshipper = next(i for i in players if i.role == Role.worshipper)
    info_type = choice(list(InformationType))
    if info_type == InformationType.two_possible_l:
        decoy = choice([i for i in players if i.role != Role.l and i.player_id != investigator.player_id])
        pair = [l_player, decoy]
        shuffle(pair)
        content = f"One of these two players is L: {pair[0].alias}, {pair[1].alias}."
    elif info_type == InformationType.two_not_l:
        candidates = [i for i in players if i.role != Role.l and i.player_id != investigator.player_id]
        pair = sample(candidates, min(2, len(candidates)))
        content = f"Neither of these players is L: {pair[0].alias}, {pair[1].alias}."
    else:
        content = f"This player is the Worshipper: {worshipper.alias}."
    return info_type, content


def generate_all_info(players: list[Player]):
    investigators = [i for i in players if i.role == Role.investigator]
    results = []
    used = set()
    for i in investigators:
        attempts = 0
        while attempts < 20:
            info_type, content = generate_investigator_info(players, i)
            if content not in used:
                used.add(content)
                results.append((i, info_type, content))
                break
            attempts += 1
        else:
            info_type, content = generate_investigator_info(players, i)
            results.append((i, info_type, content))
    return results


def assign_roles(players: list[Player]):
    roles = [Role.kira, Role.l, Role.worshipper] + [Role.investigator] * (len(players) - 3)
    shuffle(roles)
    for player, role in zip(players, roles):
        player.role = role


ROLE_INSTRUCTIONS = {
    Role.kira: (
        "**Your Mechanics:**\n"
        "• Each round, after Justice Phase, you may eliminate one player at the cost of halving your max VP.\n"
        "• If L is eliminated (by vote or by you), you win.\n"
        "• You also win if only you and L remain.\n"
        "• You have no starting information — fabricate it when someone reaches 100 Trust with you.\n"
        "• Your VP penalty stacks — use your eliminations wisely."
    ),
    Role.l: (
        "**Your Mechanics:**\n"
        "• Each round, you privately investigate one player by asking the bot if they are Kira.\n"
        "• Once you find Kira, this phase is skipped silently — don't reveal you know.\n"
        "• You lose 20 max VP each time an Investigator or Worshipper is eliminated.\n"
        "• You win if Kira is eliminated during the Justice Phase.\n"
        "• You have no starting information — fabricate it when someone reaches 100 Trust with you."
    ),
    Role.worshipper: (
        "**Your Mechanics:**\n"
        "• You know who Kira is. They do not know who you are.\n"
        "• Help Kira win without exposing yourself or them.\n"
        "• You win if L is eliminated or only Kira and L remain.\n"
        "• You have no starting information — fabricate it when someone reaches 100 Trust with you."
    ),
    Role.investigator: (
        "**Your Mechanics:**\n"
        "• You start with unique information about other players — check your message above.\n"
        "• When your Trust with another player reaches 100, you exchange information via DM.\n"
        "• Your information is pre-filled in the modal but editable — choose wisely.\n"
        "• You win if Kira is eliminated during the Justice Phase."
    ),
}


def build_trust_matrix(session, game_id: int, players: list[Player]):
    for i in range(len(players)):
        for j in range(i + 1, len(players)):
            entry = TrustMatrix(game_id=game_id, player_a_id=players[i].player_id, player_b_id=players[j].player_id, trust_a_to_b=0, trust_b_to_a=0)
            session.add(entry)


def wrong_channel(interaction: Interaction, game: Game):
    return interaction.channel_id != game.channel_id


class JoinModal(Modal):
    def __init__(self, cog, game):
        super().__init__(title="Join L's Game")
        self.cog = cog
        self.game = game
        self.alias_input = TextInput(label="Choose your alias", style=TextStyle.short, placeholder="This will be your public identity during the game.", required=True, min_length=2, max_length=32)
        self.add_item(self.alias_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            alias = self.alias_input.value.strip()
            existing_alias = session.query(Player).filter_by(game_id=game.game_id, alias=alias).first()
            existing_player = session.query(Player).filter_by(game_id=game.game_id, discord_id=interaction.user.id).first()
            if existing_alias:
                await interaction.response.send_message("That alias is already taken. Please use `/join` again and choose a different one.", ephemeral=True)
                return
            if existing_player:
                await interaction.response.send_message("You're already joined.", ephemeral=True)
                return
            try:
                await interaction.user.send(f"✅ You've joined **L's Game** as **{alias}**. Keep your DMs open — the game will contact you here.")
            except Forbidden:
                await interaction.response.send_message("A player failed to join — please ensure your DMs are open.")
                return
            player = Player(game_id=game.game_id, discord_id=interaction.user.id, display_name=interaction.user.display_name, alias=alias)
            session.add(player)
            session.commit()
            current_count = session.query(Player).filter_by(game_id=game.game_id).count()
            await interaction.response.send_message(f"A new player has joined the lobby. **{current_count}/{game.player_count}** players ready.")
        finally:
            session.close()


class GameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    
    @app_commands.command(name="setup", description="Create a new game in this channel. Admin only.")
    @app_commands.describe(players="Number of players (5-8)", timeout="Hours per turn (default 24)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: Interaction, players: int = 6, timeout: int = 24):
        await interaction.response.defer()
        if not 5 <= players <= 8:
            await interaction.followup.send("Player count must be between 5 and 8.", ephemeral=True)
            return
        session = get_session()
        try:
            existing = session.query(Game).filter_by(guild_id=interaction.guild_id).first()
            if existing:
                session.delete(existing)
                session.commit()
            game = Game(guild_id=interaction.guild_id, channel_id=interaction.channel_id, status=GameStatus.lobby, timeout_hours=timeout)
            game.player_count = players
            session.add(game)
            session.commit()
            embed = Embed(title="⚖️ L's Game — Lobby Open",
                          description=(
                              f"A new game has been created in this channel.\n"
                              f"**Players needed:** {players}\n"
                              f"**Turn timeout:** {timeout} hours\n\n"
                              f"Use `/join` to enter. Your identity will be hidden until the game ends.\n"
                              f"**Make sure your DMs are open.**"
                ),
                color=Color.dark_red())
            embed.set_footer(text="Admin: use /start when enough players have joined.")
            await interaction.followup.send(embed=embed)
        finally:
            session.close()


    @app_commands.command(name="join", description="Join the current game lobby.")
    async def join(self, interaction: Interaction):
        session = get_session()
        try:
            game = session.query(Game).filter_by(guild_id=interaction.guild_id, status=GameStatus.lobby).first()
            if not game:
                await interaction.response.send_message("No open lobby found.", ephemeral=True)
                return
            if wrong_channel(interaction, game):
                await interaction.response.send_message("Please use this command in the game channel.", ephemeral=True)
                return
            existing_player = session.query(Player).filter_by(game_id=game.game_id, discord_id=interaction.user.id).first()
            if existing_player:
                await interaction.response.send_message("You've already joined.", ephemeral=True)
                return
            current_count = session.query(Player).filter_by(game_id=game.game_id).count()
            if current_count >= game.player_count:
                await interaction.response.send_message("The lobby is full.", ephemeral=True)
                return
            await interaction.response.send_modal(JoinModal(self, game))
        finally:
            session.close()
        

    @app_commands.command(name="start", description="Start the game. Admin only.")
    @app_commands.checks.has_permissions(administrator=True)
    async def start(self, interaction: Interaction):
        await interaction.response.defer()
        session = get_session()
        try:
            game = session.query(Game).filter_by(guild_id=interaction.guild_id, status=GameStatus.lobby).first()
            if not game:
                await interaction.followup.send("No open lobby found.", ephemeral=True)
                return
            if wrong_channel(interaction, game):
                await interaction.followup.send("Please use this command in the game channel.", ephemeral=True)
                return
            required = game.player_count
            players = session.query(Player).filter_by(game_id=game.game_id).all()
            if len(players) < required:
                await interaction.followup.send(f"Not enough players. Need {required}, have {len(players)}.", ephemeral=True)
                return
            assign_roles(players)
            shuffle(players)
            for pos, player in enumerate(players):
                player.turn_order = pos
            session.flush()
            build_trust_matrix(session, game.game_id, players)
            session.flush()
            info_results = generate_all_info(players)
            for investigator, info_type, content in info_results:
                info = Information(
                    game_id=game.game_id,
                    owner_id=investigator.player_id,
                    target_id=investigator.player_id,
                    info_type=info_type,
                    content=content,
                    is_sent=True,
                )
                session.add(info)
            game.status = GameStatus.active
            game.current_phase = Phase.debate
            game.current_round = 1
            game.current_turn_index = 0
            session.commit()
            for player in players:
                member = interaction.guild.get_member(player.discord_id) or await interaction.guild.fetch_member(player.discord_id)
                if not member:
                    continue
                role_descriptions = {
                    Role.kira: "You are **Kira**. Eliminate L before you are exposed.",
                    Role.l: "You are **L**. Identify and eliminate Kira through the Justice Phase.",
                    Role.worshipper: f"You are the **Worshipper**. You serve Kira. Help them win.",
                    Role.investigator: "You are an **Investigator**. Help L find Kira.",
                }
                msg = role_descriptions[player.role]
                if player.role == Role.worshipper:
                    kira = next(p for p in players if p.role == Role.kira)
                    msg += f"\n\nKira is: **{kira.alias}**"
                if player.role == Role.investigator:
                    info = next((i for i in info_results if i[0].player_id == player.player_id), None)
                    if info:
                        msg += f"\n\nYour starting information:\n> {info[2]}"
                try:
                    await member.send(msg)
                    await member.send(ROLE_INSTRUCTIONS[player.role])
                except Forbidden:
                    pass
            first_player = players[0]
            embed = Embed(title="⚖️ L's Game — Begin",
                          description=(
                              f"The game has started. **{len(players)} players** are in.\n"
                              f"Roles have been assigned. Check your DMs.\n\n"
                              f"**Round 1 — Debate Phase**\n"
                              f"Awaiting the first player's turn."
                              ), color=Color.dark_red())
            await interaction.followup.send(embed=embed)
            first_turn = Turn(game_id=game.game_id, round=1, turn_index=0, player_id=first_player.player_id, phase=Phase.debate)
            session.add(first_turn)
            session.commit()
            await self._prompt_turn(interaction, game, first_player, session)
        finally:
            session.close()


    async def _prompt_turn(self, interaction: Interaction, game: Game, player: Player, session):
        debate_cog = self.bot.get_cog("DebateCog")
        if debate_cog:
            await debate_cog.prompt_turn(interaction.guild, game, player)

    
    @app_commands.command(name="forfeit", description="Force-forfeit a player. Admin only.")
    @app_commands.describe(member="The player to forfeit")
    @app_commands.checks.has_permissions(administrator=True)
    async def forfeit(self, interaction: Interaction, member: Member):
        session = get_session()
        try:
            game = session.query(Game).filter_by(guild_id=interaction.guild_id, status=GameStatus.active).first()
            if not game:
                await interaction.response.send_message("No active game found.", ephemeral=True)
                return
            if wrong_channel(interaction, game):
                await interaction.response.send_message("Please use this command in the game channel.", ephemeral=True)
                return
            player = session.query(Player).filter_by(game_id=game.game_id, discord_id=member.id).first()
            if not player:
                await interaction.response.send_message("That player is not in the current game.", ephemeral=True)
                return
            if player.is_eliminated:
                await interaction.response.send_message("That player is already eliminated.", ephemeral=True)
                return
            player.is_eliminated = True
            player.timeout_at = None
            session.commit()
            await interaction.response.send_message(f"**{member.display_name}** has forfeited and been removed from the game. Their remaining turns will be auto-passed.")
            current_turn = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index).first()
            if current_turn and current_turn.player_id == player.player_id:
                debate_cog = self.bot.get_cog("DebateCog")
                if debate_cog:
                    await debate_cog.advance_turn(game, session, interaction.guild)
        finally:
            session.close()


    @app_commands.command(name="resume", description="Resume the game after a bot restart. Admin only.")
    @app_commands.checks.has_permissions(administrator=True)
    async def resume(self, interaction: Interaction):
        session = get_session()
        try:
            game = session.query(Game).filter_by(guild_id=interaction.guild_id, status=GameStatus.active).first()
            if not game:
                await interaction.response.send_message("No active game found.", ephemeral=True)
                return
            if wrong_channel(interaction, game):
                await interaction.response.send_message("Please use this command in the game channel.", ephemeral=True)
                return
            await interaction.response.send_message("Resuming game...", ephemeral=True)
            guild = interaction.guild
            channel = guild.get_channel(game.channel_id)
            if game.current_phase == Phase.debate:
                current_turn = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index).first()
                if not current_turn:
                    await channel.send("⚠️ Could not find current turn record.")
                    return
                player = session.query(Player).get(current_turn.player_id)
                debate_cog = self.bot.get_cog("DebateCog")
                if debate_cog:
                    await debate_cog.prompt_turn(guild, game, player)
            elif game.current_phase == Phase.justice:
                active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).all()
                voted_ids = {i.voter_id for i in session.query(Vote).filter_by(game_id=game.game_id, round=game.current_round).all()}
                pending = [i for i in active_players if i.player_id not in voted_ids]
                options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_players]
                justice_cog = self.bot.get_cog("JusticeCog")
                if justice_cog:
                    for player in pending:
                        player_options = [i for i in options if int(i.value) != player.player_id]
                        member = guild.get_member(player.discord_id)
                        if not member:
                            continue
                        try:
                            await member.send(f"**Justice Phase — Round {game.current_round}**\nVote to eliminate a player.", view=VoteView(justice_cog, player, game, player_options))
                        except Forbidden:
                            await justice_cog._skip_voter(game, player, channel, session)
            elif game.current_phase == Phase.l_reasoning:
                active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).all()
                resolution_cog = self.bot.get_cog("ResolutionCog")
                if not resolution_cog:
                    return
                if not game.l_phase_done:
                    l_player = next((i for i in active_players if i.role == Role.l), None)
                    if l_player:
                        l_member = guild.get_member(l_player.discord_id) or await interaction.guild.fetch_member(player.discord_id)
                        if l_member:
                            options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_players if i.player_id != l_player.player_id]
                            try:
                                await l_member.send(f"**L's Reasoning — Round {game.current_round}**\nInvestigate a player.", view=InvestigateSelectView(resolution_cog, l_player, game, options))
                            except Forbidden:
                                game.l_phase_done = True
                                session.commit()
                                await resolution_cog._check_both_done(game.game_id)
                if game.kira_phase_target == 0:
                    kira_player = next((i for i in active_players if i.role == Role.kira), None)
                    if kira_player:
                        kira_member = guild.get_member(kira_player.discord_id) or await interaction.guild.fetch_member(player.discord_id)
                        if kira_member:
                            try:
                                await kira_member.send(f"**Kira's Judgment — Round {game.current_round}**\nDo you wish to eliminate a player?", view=KiraJudgmentView(resolution_cog, kira_player, game))
                            except Forbidden:
                                game.kira_phase_target = -1
                                session.commit()
                                await resolution_cog._check_both_done(game.game_id)
            await channel.send("✅ Game resumed.")
        finally:
            session.close()


    @app_commands.command(name="end", description="End the current lobby. Admin only.")
    @app_commands.checks.has_permissions(administrator=True)
    async def end(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        session = get_session()
        try:
            game = session.query(Game).filter_by(guild_id=interaction.guild_id).first()
            if not game:
                await interaction.followup.send("No open lobby found.", ephemeral=True)
                return
            game.status = GameStatus.finished
            session.commit()
            await interaction.followup.send("Game cancelled.")
        finally:
            session.close()

    
    @setup.error
    @start.error
    @forfeit.error
    @resume.error
    @end.error
    async def admin_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need administrator permissions for this.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(GameCog(bot))
