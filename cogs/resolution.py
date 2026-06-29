from discord import Interaction, Embed, Color, SelectOption, Guild, Forbidden, ButtonStyle
from discord.ext import commands
from discord.ui import View, Select, Button
from db.database import get_session
from db.models import Game, Player, Phase, Role, Turn


class InvestigateSelectView(View):
    def __init__(self, cog, player: Player, game: Game, options: list):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.player = player
        self.game = game
        select = Select(placeholder="Investigate a player...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_timeout(self):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            await self.cog.l_done(game.game_id, result=None)
        finally:
            session.close()


    async def on_select(self, interaction: Interaction):
        target_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            target = session.query(Player).get(target_id)
            is_kira = target.role == Role.kira
            if is_kira:
                await interaction.response.send_message(f"✅ Yes. **{target.alias}** is Kira.", ephemeral=True)
                game.l_knows_kira = True
                session.commit()
            else:
                await interaction.response.send_message(f"❌ No. **{target.alias}** is not Kira.", ephemeral=True)
            await self.cog.l_done(game.game_id, result=None)
        finally:
            session.close()


class KiraJudgmentView(View):
    def __init__(self, cog, player: Player, game: Game):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.player = player
        self.game = game
        yes_btn = Button(label="Eliminate a Player", style=ButtonStyle.danger)
        no_btn = Button(label="Pass", style=ButtonStyle.secondary)
        yes_btn.callback = self.on_yes
        no_btn.callback = self.on_no
        self.add_item(yes_btn)
        self.add_item(no_btn)


    async def on_timeout(self):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            await self.cog.kira_done(game.game_id, eliminated_id=None)
        finally:
            session.close()


    async def on_no(self, interaction: Interaction):
        await interaction.response.send_message("You chose not to eliminate anyone.", ephemeral=True)
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            await self.cog.kira_done(game.game_id, eliminated_id=None)
        finally:
            session.close()


    async def on_yes(self, interaction: Interaction):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            if game.kira_judgement_used:
                await interaction.response.send_message("You have already made your judgement this round.", ephemeral=True)
            active_players = session.query(Player).filter(Player.game_id == game.game_id, Player.is_eliminated == False, Player.player_id != self.player.player_id).all()
            options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_players]
            await interaction.response.send_message("Choose a player to eliminate:", view=KiraTargetView(self.cog, self.player, game, options), ephemeral=True)
        finally:
            session.close()


class KiraTargetView(View):
    def __init__(self, cog, player, game, options):
        super().__init__(timeout=300)
        self.cog = cog
        self.player = player
        self.game = game
        select = Select(placeholder="Choose a target...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        target_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            if game.kira_judgement_used:
                await interaction.response.send_message("You have already made your judgement this round.", ephemeral=True)
                return
            game.kira_judgement_used = True
            kira = session.query(Player).get(self.player.player_id)
            kira.vp_max = max(1, kira.vp_max // 2)
            kira.vp_current = min(kira.vp_current, kira.vp_max)
            session.commit()
            await interaction.response.send_message(f"Elimination confirmed. Your max VP is now {kira.vp_max}.", ephemeral=True)
            await self.cog.kira_done(game.game_id, eliminated_id=target_id)
        finally:
            session.close()


class ResolutionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    async def start_resolution(self, guild: Guild, game: Game, active_players: list, session):
        game_id = game.game_id
        game.l_phase_done = False
        game.kira_phase_target = 0
        session.commit()
        l_player = next((i for i in active_players if i.role == Role.l), None)
        kira_player = next((i for i in active_players if i.role == Role.kira), None)
        if l_player and not game.l_knows_kira:
            l_member = guild.get_member(l_player.discord_id)
            if l_member:
                options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_players if i.player_id != l_player.player_id]
                try:
                    await l_member.send(f"**L's Reasoning — Round {game.current_round}**\nInvestigate a player.", view=InvestigateSelectView(self, l_player, game, options))
                except Forbidden:
                    game.l_phase_done = True
                    session.commit()
            else:
                game.l_phase_done = True
                session.commit()
        else:
            game.l_phase_done = True
            session.commit()
        if kira_player:
            kira_member = guild.get_member(kira_player.discord_id)
            if kira_member:
                try:
                    await kira_member.send(f"**Kira's Judgment — Round {game.current_round}**\nDo you wish to eliminate a player?", view=KiraJudgmentView(self, kira_player, game))
                except Forbidden:
                    game.kira_phase_target = -1
                    session.commit()
            else:
                game.kira_phase_target = -1
                session.commit()
        await self._check_both_done(game_id)


    async def l_done(self, game_id: int, result):
        session = get_session()
        try:
            game = session.query(Game).get(game_id)
            game.l_phase_done = True
            session.commit()
            await self._check_both_done(game_id)
        finally:
            session.close()


    async def kira_done(self, game_id: int, eliminated_id):
        session = get_session()
        try:
            game = session.query(Game).get(game_id)
            game.kira_phase_target = eliminated_id if eliminated_id else -1
            session.commit()
            await self._check_both_done(game_id)
        finally:
            session.close()


    async def _check_both_done(self, game_id: int):
        session = get_session()
        try:
            game = session.query(Game).get(game_id)
            if not game.l_phase_done or game.kira_phase_target == 0:
                return
            eliminate_id = game.kira_phase_target if game.kira_phase_target > 0 else None
            guild = self.bot.get_guild(game.guild_id) or await self.bot.fetch_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
            if eliminate_id:
                target = session.query(Player).get(eliminate_id)
                target.is_eliminated = True
                session.commit()
                if target.role == Role.l:
                    embed = Embed(title="💀 L has been eliminated!", description=f"**{target.alias}** has been eliminated by Kira.\nTheir true identity: **{target.display_name}**", color=Color.dark_red())
                    await channel.send(embed=embed)
                    justice_cog = self.bot.get_cog("JusticeCog")
                    if justice_cog:
                        await justice_cog.check_win(game, session, channel, target)
                    return
                embed = Embed(title="💀 Player Eliminated by Kira", description=f"**{target.alias}** has been eliminated.\nTheir true identity: **{target.display_name}**", color=Color.dark_red())
                await channel.send(embed=embed)
                justice_cog = self.bot.get_cog("JusticeCog")
                if justice_cog:
                    if await justice_cog.check_win(game, session, channel, target):
                        return
            await self._start_next_round(game, session, guild, channel)
        finally:
            session.close()


    async def _start_next_round(self, game: Game, session, guild: Guild, channel):
        game.current_round += 1
        game.current_turn_index = 0
        game.current_phase = Phase.debate
        game.skipped_voters = 0
        game.kira_judgement_used = False
        session.commit()
        active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).order_by(Player.turn_order).all()
        if not active_players:
            return
        embed = Embed(title=f"⚖️ Round {game.current_round} — Debate Phase", description="A new round begins. The debate continues.", color=Color.dark_red())
        await channel.send(embed=embed)
        first_player = active_players[0]
        new_turn = Turn(game_id=game.game_id, round=game.current_round, turn_index=0, player_id=first_player.player_id, phase=Phase.debate)
        session.add(new_turn)
        first_player.vp_current = min(first_player.vp_current + 40, first_player.vp_max)
        session.commit()
        debate_cog = self.bot.get_cog("DebateCog")
        if debate_cog:
            await debate_cog.prompt_turn(guild, game, first_player)


async def setup(bot):
    await bot.add_cog(ResolutionCog(bot))
