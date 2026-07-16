from discord import Interaction, Embed, Color, SelectOption, Guild, Forbidden
from discord.ext import commands
from discord.ui import View, Select
from db.database import get_session
from db.models import Game, Player, Vote, GameStatus, Phase, Role
from cogs.debate import is_game_active


class VoteView(View):
    def __init__(self, cog, player: Player, game: Game, options: list):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.player = player
        self.game = game
        select = Select(placeholder="Vote to eliminate...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_timeout(self):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            player = session.query(Player).get(self.player.player_id)
            existing = session.query(Vote).filter_by(game_id=game.game_id, round=game.current_round, voter_id=player.player_id).first()
            if not existing:
                guild = self.cog.bot.get_guild(game.guild_id)
                channel = guild.get_channel(game.channel_id) if guild else None
                await self.cog._skip_voter(game, player, channel, session)
        finally:
            session.close()


    async def on_select(self, interaction: Interaction):
        if not await is_game_active(self.game.game_id):
            await interaction.response.send_message("This game is not active.", ephemeral=True)
            return
        target_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            player = session.query(Player).get(self.player.player_id)
            existing = session.query(Vote).filter_by(game_id=game.game_id, round=game.current_round, voter_id=player.player_id).first()
            if existing:
                existing.target_id = target_id
                session.commit()
                await interaction.response.send_message("Your vote has been changed.", ephemeral=True)
                return
            else:
                session.add(Vote(game_id=game.game_id, round=game.current_round, voter_id=player.player_id, target_id=target_id))
                session.commit()
                await interaction.response.send_message("Your vote has been cast.", ephemeral=True)
            await self.cog.check_votes_complete(game.game_id, interaction)
        finally:
            session.close()


class JusticeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    async def start_voting(self, guild: Guild, game: Game, active_players: list, session):
        options = [SelectOption(label=i.alias, value=str(i.player_id))for i in active_players]
        channel = guild.get_channel(game.channel_id)
        for player in active_players:
            player_options = [i for i in options if int(i.value) != player.player_id]
            member = guild.get_member(player.discord_id)
            if not member:
                await self._skip_voter(game, player, channel, session)
                continue
            try:
                await member.send(f"**Justice Phase — Round {game.current_round}**\nVote to eliminate a player.", view=VoteView(self, player, game, player_options))
            except Forbidden:
                await self._skip_voter(game, player, channel, session)


    async def check_votes_complete(self, game_id: int, interaction: Interaction = None):
        session = get_session()
        try:
            game = session.query(Game).get(game_id)
            active_players = session.query(Player).filter_by(game_id=game_id, is_eliminated=False).all()
            votes = session.query(Vote).filter_by(game_id=game_id, round=game.current_round).all()
            if len(votes) + game.skipped_voters < len(active_players):
                return
            await self._resolve_votes(game, active_players, votes, session)
        finally:
            session.close()


    async def _resolve_votes(self, game: Game, active_players: list, votes: list, session):
        guild = self.bot.get_guild(game.guild_id) or await self.bot.fetch_guild(game.guild_id)
        channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
        tally = {}
        for i in votes:
            tally[i.target_id] = tally.get(i.target_id, 0) + 1
        vote_lines = []
        for i in votes:
            voter = session.query(Player).get(i.voter_id)
            target = session.query(Player).get(i.target_id)
            vote_lines.append(f"**{voter.alias}** → {target.alias}")
        embed = Embed(title="⚖️ Justice Phase — Votes", description="\n".join(vote_lines) if vote_lines else "No votes were cast.", color=Color.dark_gold())
        await channel.send(embed=embed)
        threshold = len(active_players) / 2
        eliminated_id = None
        if tally:
            max_votes = max(tally.values())
            if max_votes >= threshold:
                top = [i for i, j in tally.items() if j == max_votes]
                if len(top) == 1:
                    eliminated_id = top[0]
        if eliminated_id:
            eliminated = session.query(Player).get(eliminated_id)
            eliminated.is_eliminated = True
            session.commit()
            embed = Embed(title="💀 Player Eliminated", description=(
                f"**{eliminated.alias}** has been eliminated.\n"
                f"Their true identity: **{eliminated.display_name}**"
                ), color=Color.dark_red())
            await channel.send(embed=embed)
            if eliminated.role in (Role.investigator, Role.worshipper):
                l_player = session.query(Player).filter_by(game_id=game.game_id, role=Role.l, is_eliminated=False).first()
                if l_player:
                    l_player.vp_max = max(0, l_player.vp_max - 20)
                    l_player.vp_current = min(l_player.vp_current, l_player.vp_max)
                    session.commit()
            if await self.check_win(game, session, channel, eliminated):
                return
        else:
            await channel.send("⚖️ **No elimination** — the vote was tied or no majority reached.")
        game.current_phase = Phase.l_reasoning
        session.commit()
        resolution_cog = self.bot.get_cog("ResolutionCog")
        if resolution_cog:
            active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).all()
            await resolution_cog.start_resolution(guild, game, active_players, session)


    async def check_win(self, game: Game, session, channel, eliminated: Player) -> bool:
        """Check win conditions after elimination. Returns True if game over."""
        active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).all()
        active_roles = [i.role for i in active_players]
        if eliminated.role == Role.kira:
            await channel.send(embed=Embed(title="🏆 L Wins!", description="**Kira** has been eliminated. Justice prevails.", color=Color.green()))
            game.status = GameStatus.finished
            session.commit()
            await self._reveal_all(game, session, channel)
            return True
        if eliminated.role == Role.l:
            await channel.send(embed=Embed(title="🏆 Kira Wins!", description="**L** has been eliminated. Kira reigns.", color=Color.dark_red()))
            game.status = GameStatus.finished
            session.commit()
            await self._reveal_all(game, session, channel)
            return True
        if set(active_roles) == {Role.kira, Role.l}:
            await channel.send(embed=Embed(title="🏆 Kira Wins!", description="Only Kira and L remain. Kira reigns.", color=Color.dark_red()))
            game.status = GameStatus.finished
            session.commit()
            await self._reveal_all(game, session, channel)
            return True
        return False


    async def _reveal_all(self, game: Game, session, channel):
        players = session.query(Player).filter_by(game_id=game.game_id).all()
        lines = [f"**{i.alias}** → {i.display_name} ({i.role.value})"for i in players]
        embed = Embed(title="📋 Game Over — Full Reveal", description="\n".join(lines), color=Color.blurple())
        await channel.send(embed=embed)


    async def _skip_voter(self, game: Game, player: Player, channel, session):
        game.skipped_voters += 1
        session.commit()
        await self.check_votes_complete(game.game_id)


async def setup(bot):
    await bot.add_cog(JusticeCog(bot))

