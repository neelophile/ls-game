from discord import app_commands, Interaction, Embed, Color, SelectOption, TextStyle, Guild, ButtonStyle
from discord.ext import commands
from discord.ui import View, Select, Modal, TextInput, Button
from db.database import get_session
from db.models import utcnow, Game, Player, Turn, Proposal, ProposalResponse, Argument, Rebuttal, GameStatus, Phase, Role, Tone, RemarkType, ProposalStatus, ArgumentStatus, TrustMatrix, Information, InformationType
from random import shuffle
from datetime import timedelta


TONE_CONFIG = {
    Tone.weak_accusation: {"label": "Weak Accusation", "min_vp": 5, "vp_per_word": 1, "effect_per_word": 1, "suspicion": +1, "trust": -1},
    Tone.powerful_accusation: {"label": "Powerful Accusation", "min_vp": 20, "vp_per_word": 4, "effect_per_word": 4, "suspicion": +4, "trust": -4},
    Tone.gentle_accusation: {"label": "Gentle Accusation", "min_vp": 20, "vp_per_word": 4, "effect_per_word": 1, "suspicion": +1, "trust": 0},
    Tone.careful_accusation: {"label": "Careful Accusation", "min_vp": 40, "vp_per_word": 8, "effect_per_word": 4, "suspicion": +4, "trust": 0},
    Tone.weak_commendation: {"label": "Weak Commendation", "min_vp": 5, "vp_per_word": 1, "effect_per_word": 1,  "suspicion": -1, "trust": +1},
    Tone.powerful_commendation: {"label": "Powerful Commendation", "min_vp": 20, "vp_per_word": 4, "effect_per_word": 4, "suspicion": -4, "trust": +4}
}
REMARK_CONFIG = {
    RemarkType.raise_suspicion: {"label": "Raise Everyone's Suspicion", "min_vp": 70, "vp_per_word": 14, "effect_per_word": 1},
    RemarkType.lower_suspicion: {"label": "Lower Everyone's Suspicion", "min_vp": 70, "vp_per_word": 14, "effect_per_word": -1},
    RemarkType.restore_vp: {"label": "Restore VP", "min_vp": 40, "vp_per_word": 8, "effect_per_word": None},
    RemarkType.raise_max_vp: {"label": "Raise Max VP", "min_vp": 70, "vp_per_word": 14, "effect_per_word": None}
}
AGREE_MIN_VP = 20
AGREE_VP_PER_WORD = 4
DISAGREE_MIN_VP = 20
DISAGREE_VP_PER_WORD = 4


def count_words(text: str):
    return len(text.strip().split())


def calc_vp_cost(min_vp: int, vp_per_word: int, word_count: int):
    return max(min_vp, vp_per_word * word_count)


def calc_effectiveness(min_vp: int, vp_per_word: int, effect_per_word: int, word_count: int) -> int:
    effective_words = max(min_vp // vp_per_word, word_count)
    return effective_words * effect_per_word


def apply_proposal_effects(target: Player, tone: Tone, effectiveness: int, reversed: bool = False):
    cfg = TONE_CONFIG[tone]
    susp_delta = cfg["suspicion"] * effectiveness
    trust_delta = cfg["trust"] * effectiveness
    if reversed:
        susp_delta = -susp_delta
        trust_delta = -trust_delta
    target.suspicion = max(0, min(100, target.suspicion + susp_delta))
    return susp_delta, trust_delta


def get_trust(session, game_id: int, from_id: int, to_id: int):
    a, b = min(from_id, to_id), max(from_id, to_id)
    row = session.query(TrustMatrix).filter_by(game_id=game_id, player_a_id=a, player_b_id=b).first()
    if not row:
        return 0
    return row.trust_a_to_b if from_id == a else row.trust_b_to_a


def update_trust(session, game_id: int, from_id: int, to_id: int, delta: int):
    a, b = min(from_id, to_id), max(from_id, to_id)
    row = session.query(TrustMatrix).filter_by(game_id=game_id, player_a_id=a, player_b_id=b).first()
    if not row:
        return 0
    if from_id == a:
        row.trust_a_to_b = max(0, min(100, row.trust_a_to_b + delta))
        return row.trust_a_to_b
    else:
        row.trust_b_to_a = max(0, min(100, row.trust_b_to_a + delta))
        return row.trust_b_to_a


def suspicion_checkmarks(suspicion: int):
    if suspicion >= 80:
        return "✔✔"
    elif suspicion >= 40:
        return "✔"
    return ""


class ActionSelectView(View):
    def __init__(self, cog, player: Player, game: Game):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.player = player
        self.game = game
        select = Select(placeholder="Choose your options...", options=[
            SelectOption(label="Proposal", value="proposal", description="Target a player with a Tone"),
            SelectOption(label="Remark", value="remark", description="Address the group"),
            SelectOption(label="Pass", value="pass", description="Say nothing")
            ])
        select.callback = self.on_select
        self.add_item(select)


    async def on_timeout(self):
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            if player and not player.is_eliminated:
                await self.cog.auto_pass(self.game.game_id, player)
        finally:
            session.close()


    async def on_select(self, interaction: Interaction):
        value = interaction.data["values"][0]
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            game = session.query(Game).get(self.game.game_id)
            if value == "pass":
                await interaction.response.send_message("You passed your turn.", ephemeral=True)
                await self.cog.complete_turn(game, player, "pass", session)
            elif value == "proposal":
                await interaction.response.send_message("Choose your tone for your proposal:", view=ToneSelectView(self.cog, player, game), ephemeral=True)
            elif value == "remark":
                await interaction.response.send_message("Choose a remark type:", view=RemarkSelectView(self.cog, player, game), ephemeral=True)
        finally:
            session.close()


class TargetSelectView(View):
    def __init__(self, cog, player, game, tone, options):
        super().__init__(timeout=300)
        self.cog = cog
        self.player = player
        self.game = game
        self.tone = tone
        select = Select(placeholder="Choose a target...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        target_id = int(interaction.data["values"][0])
        cfg = TONE_CONFIG[self.tone]
        await interaction.response.send_modal(ProposalModal(self.cog, self.player, self.game, self.tone, target_id, cfg))


class ToneSelectView(View):
    def __init__(self, cog, player, game):
        super().__init__(timeout=300)
        self.cog = cog
        self.player = player
        self.game = game
        options = [SelectOption(label=cfg["label"], value=tone.value, description=f"Min {cfg['min_vp']} VP, {cfg['vp_per_word']} VP/word") for tone, cfg in TONE_CONFIG.items()]
        select = Select(placeholder="Choose a Tone...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        tone = Tone(interaction.data["values"][0])
        session = get_session()
        try:
            players = session.query(Player).filter_by(game_id=self.game.game_id, is_eliminated=False).filter(Player.player_id != self.player.player_id).all()
            options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in players]
            await interaction.response.send_message("Choose a target:", view=TargetSelectView(self.cog, self.player, self.game, tone, options), ephemeral=True)
        finally:
            session.close()


class ProposalModal(Modal):
    def __init__(self, cog, player, game, tone, target_id, cfg):
        super().__init__(title=f"Proposal — {cfg['label']}")
        self.cog = cog
        self.player = player
        self.game = game
        self.tone = tone
        self.target_id = target_id
        self.cfg = cfg
        self.message_input = TextInput(label="Your message", style=TextStyle.paragraph, placeholder=f"Min {cfg['min_vp']} VP, {cfg['vp_per_word']} VP/word", required=True, max_length=500)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            game = session.query(Game).get(self.game.game_id)
            target = session.query(Player).get(self.target_id)
            text = self.message_input.value
            wc = count_words(text)
            cost = calc_vp_cost(self.cfg["min_vp"], self.cfg["vp_per_word"], wc)
            if player.vp_current < cost:
                await interaction.response.send_message(f"Not enough VP. This message costs **{cost} VP** but you have **{player.vp_current} VP**.\nPlease choose a new action.", view=ActionSelectView(self.cog, player, game), ephemeral=True)
                return
            player.vp_current -= cost
            effectiveness = calc_effectiveness(self.cfg["min_vp"], self.cfg["vp_per_word"], self.cfg["effect_per_word"], wc)
            turn = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index).first()
            proposal = Proposal(game_id=game.game_id, turn_id=turn.turn_id, proposer_id=player.player_id, target_id=target.player_id, tone=self.tone, message=text, word_count=wc, vp_spent=cost, effectiveness=effectiveness, status=ProposalStatus.pending)
            session.add(proposal)
            session.flush()
            others = session.query(Player).filter(Player.game_id == game.game_id, Player.is_eliminated == False, Player.player_id != player.player_id).all()
            shuffle(others)
            for i in others:
                session.add(ProposalResponse(proposal_id=proposal.proposal_id, player_id=i.player_id))
            turn.action_taken = "proposal"
            session.commit()
            channel = interaction.guild.get_channel(game.channel_id)
            cfg_label = self.cfg["label"]
            marks = suspicion_checkmarks(target.suspicion)
            embed = Embed(title=f"📜 Proposal — {cfg_label}",
                          description=(
                              f"**Target:** {target.alias} {marks}\n\n"
                              f"*\"{text}\"*"
                              ), color=Color.dark_red())
            await channel.send(embed=embed)
            await interaction.response.send_message("Proposal submitted.", ephemeral=True)
            await self.cog.contact_next_for_proposal(interaction, proposal.proposal_id, session)
        finally:
            session.close()


class AgreeDisagreeView(View):
    def __init__(self, cog, proposal_id: int, player: Player, game: Game):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.proposal_id = proposal_id
        self.player = player
        self.game = game
        select = Select(placeholder="Respond to the Proposal...", options=[
            SelectOption(label="Agree", value="agree"),
            SelectOption(label="Disagree", value="disagree"),
            SelectOption(label="Pass", value="pass")])
        select.callback = self.on_select
        self.add_item(select)


    async def on_timeout(self):
        session = get_session()
        try:
            response = session.query(ProposalResponse).filter_by(proposal_id=self.proposal_id, player_id=self.player.player_id, response=None).first()
            if response:
                response.response = "pass"
                response.responded_at = utcnow()
                session.commit()
                await self.cog.continue_proposal_chain(self.proposal_id)
        finally:
            session.close()


    async def on_select(self, interaction: Interaction):
        value = interaction.data["values"][0]
        session = get_session()
        try:
            response = session.query(ProposalResponse).filter_by(proposal_id=self.proposal_id, player_id=self.player.player_id).first()
            if not response or response.response is not None:
                await interaction.response.send_message("This proposal has already been resolved.", ephemeral=True)
                return
            response.response = value
            response.responded_at = utcnow()
            if value == "agree":
                await interaction.response.send_modal(AgreeModal(self.cog, self.proposal_id, self.player, self.game, session))
            elif value == "disagree":
                await interaction.response.send_modal(DisagreeModal(self.cog, self.proposal_id, self.player, self.game, session))
            else:
                session.commit()
                await interaction.response.send_message("You passed.", ephemeral=True)
                await self.cog.contact_next_for_proposal(interaction, self.proposal_id, session)
        finally:
            session.close()


class AgreeModal(Modal):
    def __init__(self, cog, proposal_id, player, game, session):
        super().__init__(title="Agree — Add a message")
        self.cog = cog
        self.proposal_id = proposal_id
        self.player = player
        self.game = game
        self.message_input = TextInput(label="Your message (min 20 VP, 4 VP/word)", style=TextStyle.paragraph, required=True, max_length=300)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            game = session.query(Game).get(self.game.game_id)
            text = self.message_input.value
            wc = count_words(text)
            cost = calc_vp_cost(AGREE_MIN_VP, AGREE_VP_PER_WORD, wc)
            if player.vp_current < cost:
                await interaction.response.send_message(f"Not enough VP. Costs **{cost} VP**, you have **{player.vp_current} VP**. You are auto-passed.", ephemeral=True)
                response = session.query(ProposalResponse).filter_by(proposal_id=self.proposal_id, player_id=player.player_id).first()
                if response:
                    response.response = "pass"
                    response.responded_at = utcnow()
                session.commit()
                await self.cog.contact_next_for_proposal(interaction, self.proposal_id, session)
                return
            player.vp_current -= cost
            session.commit()
            proposal = session.query(Proposal).get(self.proposal_id)
            proposal.status = ProposalStatus.agreed
            session.commit()
            channel = interaction.guild.get_channel(game.channel_id)
            embed = Embed(title="✅ Proposal Agreed", description=f"*\"{text}\"*", color=Color.green())
            await channel.send(embed=embed)
            await interaction.response.send_message("You agreed.", ephemeral=True)
            await self.cog.resolve_proposal(interaction, proposal, reversed=False, session=session)
        finally:
            session.close()


class DisagreeModal(Modal):
    def __init__(self, cog, proposal_id, player, game, session):
        super().__init__(title="Disagree — State your case")
        self.cog = cog
        self.proposal_id = proposal_id
        self.player = player
        self.game = game
        self.message_input = TextInput(label="Your message (min 20 VP, 4 VP/word)", style=TextStyle.paragraph, required=True, max_length=300)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            game = session.query(Game).get(self.game.game_id)
            text = self.message_input.value
            wc = count_words(text)
            cost = calc_vp_cost(DISAGREE_MIN_VP, DISAGREE_VP_PER_WORD, wc)
            if player.vp_current < cost:
                await interaction.response.send_message(f"Not enough VP. Costs **{cost} VP**, you have **{player.vp_current} VP**. You are auto-passed.", ephemeral=True)
                response = session.query(ProposalResponse).filter_by(proposal_id=self.proposal_id, player_id=player.player_id).first()
                if response:
                    response.response = "pass"
                    response.responded_at = utcnow()
                session.commit()
                await self.cog.contact_next_for_proposal(interaction, self.proposal_id, session)
                return
            player.vp_current -= cost
            session.commit()
            proposal = session.query(Proposal).get(self.proposal_id)
            proposal.status = ProposalStatus.disagreed
            session.commit()
            argument = Argument(proposal_id=proposal.proposal_id, defender_id=proposal.proposer_id, attacker_id=player.player_id, status=ArgumentStatus.active, current_rebuttal_number=1, total_word_count=0)
            session.add(argument)
            session.flush()
            session.add(Rebuttal(argument_id=argument.argument_id, player_id=player.player_id, rebuttal_number=0, message=text, word_count=wc, vp_spent=cost, is_pass=False))
            session.commit()
            channel = interaction.guild.get_channel(game.channel_id)
            embed = Embed(title="⚔️ Argument Started",
                          description=(
                              f"**Attacker:** {player.alias}\n"
                              f"**Defender:** {proposal.proposer.alias}\n\n"
                              f"*\"{text}\"*"), color=Color.orange())
            await channel.send(embed=embed)
            await interaction.response.send_message("Argument started.", ephemeral=True)
            await self.cog.prompt_rebuttal(interaction, argument.argument_id, session)
        finally:
            session.close()


class RemarkSelectView(View):
    def __init__(self, cog, player, game):
        super().__init__(timeout=300)
        self.cog = cog
        self.player = player
        self.game = game
        options = [SelectOption(label=cfg["label"], value=rtype.value, description=f"Min {cfg['min_vp']} VP, {cfg['vp_per_word']} VP/word") for rtype, cfg in REMARK_CONFIG.items()]
        select = Select(placeholder="Choose a Remark type...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        rtype = RemarkType(interaction.data["values"][0])
        cfg = REMARK_CONFIG[rtype]
        await interaction.response.send_modal(RemarkModal(self.cog, self.player, self.game, rtype, cfg))


class RemarkModal(Modal):
    def __init__(self, cog, player, game, rtype, cfg):
        super().__init__(title=f"Remark — {cfg['label']}")
        self.cog = cog
        self.player = player
        self.game = game
        self.rtype = rtype
        self.cfg = cfg
        self.message_input = TextInput(label="Your message", style=TextStyle.paragraph, placeholder=f"Min {cfg['min_vp']} VP, {cfg['vp_per_word']} VP/word. No player names.", required=True, max_length=500)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            game = session.query(Game).get(self.game.game_id)
            text = self.message_input.value
            wc = count_words(text)
            cfg = self.cfg
            cost = calc_vp_cost(cfg["min_vp"], cfg["vp_per_word"], wc)
            if player.vp_current < cost:
                await interaction.response.send_message(f"Not enough VP. Costs **{cost} VP**, you have **{player.vp_current} VP**.\nPlease choose a new action.", view=ActionSelectView(self.cog, player, game), ephemeral=True)
                return
            player.vp_current -= cost
            all_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).all()
            if self.rtype == RemarkType.raise_suspicion:
                delta = wc * cfg["effect_per_word"]
                for i in all_players:
                    i.suspicion = max(0, min(100, i.suspicion + delta))
            elif self.rtype == RemarkType.lower_suspicion:
                delta = wc * abs(cfg["effect_per_word"])
                for i in all_players:
                    i.suspicion = max(0, min(100, i.suspicion - delta))
            elif self.rtype == RemarkType.restore_vp:
                player.vp_current = player.vp_max
            elif self.rtype == RemarkType.raise_max_vp:
                player.vp_max += 10
                player.vp_current = min(player.vp_current, player.vp_max)
            turn = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index).first()
            if turn:
                turn.action_taken = "remark"
            session.commit()
            channel = interaction.guild.get_channel(game.channel_id)
            embed = Embed(title=f"🗣️ Remark — {cfg['label']}", description=f"*\"{text}\"*", color=Color.blurple())
            await channel.send(embed=embed)
            await interaction.response.send_message("Remark submitted.", ephemeral=True)
            await self.cog.complete_turn(game, player, "remark", session)
        finally:
            session.close()


class RebuttalView(View):
    def __init__(self, cog, argument_id: int, player: Player, game: Game, rebuttal_number: int):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.argument_id = argument_id
        self.player = player
        self.game = game
        self.rebuttal_number = rebuttal_number
        min_vp = 5 * rebuttal_number
        vp_per_word = rebuttal_number
        select = Select(placeholder="Choose your move...", options=[
            SelectOption(label="Maintain", value="maintain",  description=f"Min {min_vp} VP, {vp_per_word} VP/word"),
            SelectOption(label="Pass", value="pass", description="Concede this argument")])
        select.callback = self.on_select
        self.add_item(select)


    async def on_timeout(self):
        session = get_session()
        try:
            argument = session.query(Argument).get(self.argument_id)
            if argument and argument.status == ArgumentStatus.active:
                await self.cog.resolve_argument_pass(self.argument_id, self.player.player_id)
        finally:
            session.close()


    async def on_select(self, interaction: Interaction):
        value = interaction.data["values"][0]
        if value == "pass":
            await interaction.response.send_message("You passed the argument.", ephemeral=True)
            await self.cog.resolve_argument_pass(self.argument_id, self.player.player_id, interaction)
        else:
            min_vp = 5 * self.rebuttal_number
            vp_per_word = self.rebuttal_number
            await interaction.response.send_modal(MaintainModal(self.cog, self.argument_id, self.player, self.game, self.rebuttal_number, min_vp, vp_per_word))


class MaintainModal(Modal):
    def __init__(self, cog, argument_id, player, game, rebuttal_number, min_vp, vp_per_word):
        super().__init__(title=f"Rebuttal {rebuttal_number} — Maintain")
        self.cog = cog
        self.argument_id = argument_id
        self.player = player
        self.game = game
        self.rebuttal_number = rebuttal_number
        self.min_vp = min_vp
        self.vp_per_word = vp_per_word
        self.message_input = TextInput(label=f"Your rebuttal (min {min_vp} VP, {vp_per_word} VP/word)", style=TextStyle.paragraph, required=True, max_length=500)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            player = session.query(Player).get(self.player.player_id)
            game = session.query(Game).get(self.game.game_id)
            argument = session.query(Argument).get(self.argument_id)
            text = self.message_input.value
            wc = count_words(text)
            cost = calc_vp_cost(self.min_vp, self.vp_per_word, wc)
            if player.vp_current < cost:
                await interaction.response.send_message(f"Not enough VP. Costs **{cost} VP**, you have **{player.vp_current} VP**.\nYou are forced to Pass.", ephemeral=True)
                await self.cog.resolve_argument_pass(self.argument_id, player.player_id, interaction)
                return
            player.vp_current -= cost
            argument.total_word_count += wc
            argument.current_rebuttal_number += 1
            session.add(Rebuttal(argument_id=argument.argument_id, player_id=player.player_id, rebuttal_number=self.rebuttal_number, message=text, word_count=wc, vp_spent=cost, is_pass=False))
            session.commit()
            role_label = "Defender" if player.player_id == argument.defender_id else "Attacker"
            channel = interaction.guild.get_channel(game.channel_id)
            embed = Embed(title=f"⚔️ Rebuttal {self.rebuttal_number} — {role_label}", description=f"*\"{text}\"*", color=Color.orange())
            await channel.send(embed=embed)
            await interaction.response.send_message("Rebuttal submitted.", ephemeral=True)
            await self.cog.prompt_rebuttal(interaction, self.argument_id, session)
        finally:
            session.close()


class DebateCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    async def prompt_turn(self, guild: Guild, game: Game, player: Player):
        member = guild.get_member(player.discord_id)
        if not member:
            return
        try:
            await member.send(f"**Your turn!** — Round {game.current_round}, Debate Phase\nVP: **{player.vp_current}/{player.vp_max}**", view=ActionSelectView(self, player, game))
            session = get_session()
            try:
                player_db = session.query(Player).get(player.player_id)
                player_db.timeout_at = utcnow() + timedelta(hours=game.timeout_hours)
                session.commit()
            finally:
                session.close()
        except Forbidden:
            channel = guild.get_channel(game.channel_id)
            if channel:
                await channel.send(f"{member.mention} — your DMs are closed. Please open them to continue playing.")


    async def auto_pass(self, game_id: int, player: Player):
        session = get_session()
        try:
            game = session.query(Game).get(game_id)
            turn = session.query(Turn).filter_by(game_id=game_id, round=game.current_round, turn_index=game.current_turn_index).first()
            if turn:
                turn.action_taken = "pass"
            session.commit()
            guild = self.bot.get_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id) if guild else None
            if channel:
                await channel.send(f"A player's turn timed out and was auto-passed.")
            await self.advance_turn(game, session, guild)
        finally:
            session.close()


    async def complete_turn(self, game: Game, player: Player, action: str, session):
        turn = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index).first()
        if turn:
            turn.action_taken = action
            turn.completed_at = utcnow()
        session.commit()
        guild = self.bot.get_guild(game.guild_id)
        await self.advance_turn(game, session, guild)


    async def advance_turn(self, game: Game, session, guild: Guild):
        """Move to the next turn or phase."""
        active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).order_by(Player.turn_order).all()
        turns_this_round = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round,phase=Phase.debate).count()
        total_turns_needed = len(active_players) * 3
        if turns_this_round >= total_turns_needed:
            for i in active_players:
                i.vp_current = i.vp_max
            game.current_phase = Phase.justice
            session.commit()
            channel = guild.get_channel(game.channel_id)
            embed = Embed(title="⚖️ Justice Phase", description="The Debate Phase has ended. All players must now vote to eliminate someone.", color=Color.dark_gold())
            await channel.send(embed=embed)
            justice_cog = self.bot.get_cog("JusticeCog")
            if justice_cog:
                await justice_cog.start_voting(guild, game, active_players, session)
        else:
            current_turn = session.query(Turn).filter_by(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index).first()
            current_pos = next((i for i, j in enumerate(active_players) if j.player_id == current_turn.player_id), None)
            if current_pos is None:
                next_player = active_players[0]
            else:
                next_player = active_players[(current_pos + 1) % len(active_players)]
            game.current_turn_index += 1
            next_player.vp_current = min(next_player.vp_current + 40, next_player.vp_max)
            new_turn = Turn(game_id=game.game_id, round=game.current_round, turn_index=game.current_turn_index, player_id=next_player.player_id, phase=Phase.debate)
            session.add(new_turn)
            session.commit()
            await self.prompt_turn(guild, game, next_player)


    async def contact_next_for_proposal(self, interaction: Interaction, proposal_id: int, session):
        proposal = session.query(Proposal).get(proposal_id)
        if proposal.status != ProposalStatus.pending:
            return
        next_response = session.query(ProposalResponse).filter_by(proposal_id=proposal_id, response=None).first()
        if not next_response:
            proposal.status = ProposalStatus.passed_through
            session.commit()
            await self.resolve_proposal(interaction, proposal, reversed=False, session=session)
            return
        next_response.contacted_at = utcnow()
        session.commit()
        player = session.query(Player).get(next_response.player_id)
        game = session.query(Game).get(proposal.game_id)
        member = interaction.guild.get_member(player.discord_id)
        if not member:
            next_response.response = "pass"
            next_response.responded_at = utcnow()
            session.commit()
            await self.contact_next_for_proposal(interaction, proposal_id, session)
            return
        try:
            await member.send(f"A Proposal has been made. Do you Agree, Disagree, or Pass?", view=AgreeDisagreeView(self, proposal_id, player, game))
        except Forbidden:
            next_response.response = "pass"
            next_response.responded_at = utcnow()
            session.commit()
            await self.contact_next_for_proposal(interaction, proposal_id, session)


    async def continue_proposal_chain(self, proposal_id: int):
        session = get_session()
        try:
            proposal = session.query(Proposal).get(proposal_id)
            game = session.query(Game).get(proposal.game_id)
            guild = self.bot.get_guild(game.guild_id)
            if not guild:
                return
            next_response = session.query(ProposalResponse).filter_by(proposal_id=proposal_id, response=None).first()
            if not next_response:
                proposal.status = ProposalStatus.passed_through
                session.commit()
                channel = guild.get_channel(game.channel_id)
                target = session.query(Player).get(proposal.target_id)
                await self.apply_and_announce_proposal(channel, proposal, target, reversed=False, session=session)
                await self.advance_turn(game, session, guild)
                return
            next_response.contacted_at = utcnow()
            session.commit()
            player = session.query(Player).get(next_response.player_id)
            member = guild.get_member(player.discord_id)
            if not member:
                next_response.response = "pass"
                next_response.responded_at = utcnow()
                session.commit()
                await self.continue_proposal_chain(proposal_id)
                return
            try:
                await member.send("A Proposal has been made. Do you Agree, Disagree, or Pass?", view=AgreeDisagreeView(self, proposal_id, player, game))
            except Forbidden:
                next_response.response = "pass"
                next_response.responded_at = utcnow()
                session.commit()
                await self.continue_proposal_chain(proposal_id)
        finally:
            session.close()


    async def resolve_proposal(self, interaction: Interaction, proposal: Proposal, reversed: bool, session):
        game = session.query(Game).get(proposal.game_id)
        target = session.query(Player).get(proposal.target_id)
        channel = interaction.guild.get_channel(game.channel_id)
        await self.apply_and_announce_proposal(channel, proposal, target, reversed, session)
        if reversed:
            proposal.status = ProposalStatus.reversed
        else:
            proposal.status = ProposalStatus.passed_through
        session.commit()
        guild = interaction.guild
        await self.advance_turn(game, session, guild)


    async def apply_and_announce_proposal(self, channel, proposal, target, reversed: bool, session):
        effectiveness = proposal.effectiveness
        susp_delta, trust_delta = apply_proposal_effects(target, proposal.tone, effectiveness, reversed)
        proposer_id = proposal.proposer_id
        new_trust = update_trust(session, proposal.game_id, proposer_id, target.player_id, trust_delta)
        session.commit()
        old_susp = target.suspicion - susp_delta
        marks = suspicion_checkmarks(target.suspicion)
        milestone_msg = ""
        if old_susp < 40 <= target.suspicion:
            milestone_msg = f"⚠️ **{target.alias}** has reached 40 Suspicion! {marks}"
        elif old_susp < 80 <= target.suspicion:
            milestone_msg = f"🚨 **{target.alias}** has reached 80 Suspicion! {marks}"
        result = "reversed" if reversed else "passed"
        embed = Embed(title=f"📋 Proposal {result.capitalize()}", description=(
            f"**Target:** {target.alias} {marks}\n"
            f"Suspicion change: {susp_delta:+}\n"
            f"Trust: {get_trust(session, proposal.game_id, proposer_id, target.player_id)} ({trust_delta:+})"), color=Color.red() if susp_delta > 0 else Color.green())
        await channel.send(embed=embed)
        if milestone_msg:
            await channel.send(milestone_msg)
        await self.check_trust_100(channel, proposal.game_id, proposer_id, target.player_id, new_trust, session)


    async def check_trust_100(self, channel, game_id, from_id, to_id, new_trust, session):
        if new_trust < 100:
            return
        from_player = session.query(Player).get(from_id)
        to_player = session.query(Player).get(to_id)
        await channel.send(f"🤝 **{from_player.alias}** and **{to_player.alias}** have reached 100 Trust!")
        guild = channel.guild
        await self.trigger_info_exchange(guild, game_id, sender=to_player, receiver=from_player, session=session)


    async def trigger_info_exchange(self, guild, game_id, sender: Player, receiver: Player, session):
        sender_member = guild.get_member(sender.discord_id)
        if not sender_member:
            return
        existing_info = session.query(Information).filter_by(game_id=game_id, owner_id=sender.player_id, target_id=sender.player_id).first()
        prefill = existing_info.content if existing_info else ""
        try:
            await sender_member.send(f"**{receiver.alias}** has reached 100 Trust with you. Send them your information:", view=InfoSubmitView(self, game_id, sender, receiver, prefill))
        except Forbidden:
            pass


    async def prompt_rebuttal(self, interaction: Interaction, argument_id: int, session):
        argument = session.query(Argument).get(argument_id)
        game = session.query(Game).get(session.query(Proposal).get(argument.proposal_id).game_id)
        is_defender = argument.current_rebuttal_number % 2 == 1
        player_id = argument.defender_id if is_defender else argument.attacker_id
        player = session.query(Player).get(player_id)
        member = interaction.guild.get_member(player.discord_id)
        if not member:
            await self.resolve_argument_pass(argument_id, player_id, interaction)
            return
        try:
            await member.send(f"**Rebuttal {argument.current_rebuttal_number}** — {'Defend' if is_defender else 'Attack'}\nVP: **{player.vp_current}/{player.vp_max}**", view=RebuttalView(self, argument_id, player, game, argument.current_rebuttal_number))
        except Forbidden:
            await self.resolve_argument_pass(argument_id, player_id, interaction)


    async def resolve_argument_pass(self, argument_id: int, passer_id: int, interaction: Interaction = None):
        session = get_session()
        try:
            argument = session.query(Argument).get(argument_id)
            argument.status = ArgumentStatus.resolved
            proposal = session.query(Proposal).get(argument.proposal_id)
            game = session.query(Game).get(proposal.game_id)
            proposal.effectiveness += argument.total_word_count
            reversed = (passer_id == argument.defender_id)
            session.commit()
            if interaction:
                guild = interaction.guild
            else:
                guild = self.bot.get_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id)
            result_text = "reversed" if reversed else "upheld"
            embed = Embed(title=f"⚔️ Argument Resolved — Proposal {result_text.capitalize()}", color=Color.red() if reversed else Color.green())
            await channel.send(embed=embed)
            target = session.query(Player).get(proposal.target_id)
            await self.apply_and_announce_proposal(channel, proposal, target, reversed, session)
            await self.advance_turn(game, session, guild)
        finally:
            session.close()


class InfoSubmitView(View):
    def __init__(self, cog, game_id, sender, receiver, prefill):
        super().__init__(timeout=86400)
        self.cog = cog
        self.game_id = game_id
        self.sender = sender
        self.receiver = receiver
        self.prefill = prefill
        btn = Button(label="Submit Information", style=ButtonStyle.primary)
        btn.callback = self.on_click
        self.add_item(btn)


    async def on_click(self, interaction: Interaction):
        await interaction.response.send_modal(InfoModal(self.cog, self.game_id, self.sender, self.receiver, self.prefill))


class InfoModal(Modal):
    def __init__(self, cog, game_id, sender, receiver, prefill):
        super().__init__(title="Send Information")
        self.cog = cog
        self.game_id = game_id
        self.sender = sender
        self.receiver = receiver
        self.info_input = TextInput(
            label=f"Information for {receiver.alias}", style=TextStyle.paragraph, default=prefill, required=True, max_length=300)
        self.add_item(self.info_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            info = Information(game_id=self.game_id, owner_id=self.sender.player_id, target_id=self.receiver.player_id, content=self.info_input.value, is_sent=True)
            session.add(info)
            session.commit()
            receiver_member = interaction.guild.get_member(self.receiver.discord_id)
            if receiver_member:
                try:
                    await receiver_member.send(f"📨 **Information received from a player:**\n> {self.info_input.value}")
                except Forbidden:
                    pass
            await interaction.response.send_message("Information sent.", ephemeral=True)
        finally:
            session.close()


async def setup(bot):
    await bot.add_cog(DebateCog(bot))

