from random import choices, choice
from discord import Interaction, Embed, Color, SelectOption, Guild, Forbidden, ButtonStyle, TextStyle
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
from db.models import utcnow, Game, Player, Item, Dinner, Telephone, ItemType, ItemRarity, DinnerStatus, Phase
from db.database import get_session
from cogs.debate import suspicion_checkmarks, is_game_active


RARITY_WEIGHTS = {
    ItemRarity.common: 60,
    ItemRarity.uncommon: 30,
    ItemRarity.rare: 10
}

ITEMS_BY_RARITY = {
    ItemRarity.common: [ItemType.tip_off, ItemType.red_herring],
    ItemRarity.uncommon: [ItemType.alias_swap, ItemType.telephone],
    ItemRarity.rare: [ItemType.wiretap, ItemType.notebook_page]
}

ITEM_LABELS = {
    ItemType.tip_off: "📌 Tip-off",
    ItemType.red_herring: "🐟 Red Herring",
    ItemType.alias_swap: "🎭 Alias Swap",
    ItemType.telephone: "📞 Telephone",
    ItemType.wiretap: "🎧 Wiretap",
    ItemType.notebook_page: "📓 Notebook Page"
}

ITEM_DESCRIPTIONS = {
    ItemType.tip_off: "Anonymously raise one player's Suspicion by 20." ,
    ItemType.red_herring: "Raise your own Suspicion by 30. In return, L's next investigation on you will return No.",
    ItemType.alias_swap: "Change your alias to something else for one round.",
    ItemType.telephone: "Send a message through a n-hop chain (n is the number of players).",
    ItemType.wiretap: "Intercept a message between a 100-Trust pair or a Dinner pair.",
    ItemType.notebook_page: "Eliminate a player outside Judgement Phase."
}

RARITY_LABELS = {
    ItemRarity.common: "Common",
    ItemRarity.uncommon: "Uncommon",
    ItemRarity.rare: "Rare"
}


class DinnerResponseView(View):
    def __init__(self, cog, dinner_id: int, game: Game):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.dinner_id = dinner_id
        self.game = game
        accept_btn = Button(label="Accept", style=ButtonStyle.success)
        decline_btn = Button(label="Decline", style=ButtonStyle.danger)
        accept_btn.callback = self.on_accept
        decline_btn.callback = self.on_decline
        self.add_item(accept_btn)
        self.add_item(decline_btn)


    async def on_accept(self, interaction: Interaction):
        session = get_session()
        try:
            dinner = session.query(Dinner).get(self.dinner_id)
            if dinner.status != DinnerStatus.pending:
                await interaction.response.send_message("This invitation has already been resolved.", ephemeral=True)
                return
            game = session.query(Game).get(dinner.game_id)
            inviter = session.query(Player).get(dinner.inviter_id)
            invitee = session.query(Player).get(dinner.invitee_id)
            guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
            thread = await channel.create_thread(name=f"🍽️ {inviter.alias} & {invitee.alias}", auto_archive_duration=1440, reason="L's Game Dinner")
            inviter_member = guild.get_member(inviter.discord_id) or await guild.fetch_member(inviter.discord_id)
            invitee_member = guild.get_member(invitee.discord_id) or await guild.fetch_member(invitee.discord_id)
            await thread.add_user(inviter_member)
            await thread.add_user(invitee_member)
            await thread.send(f"🍽️ **Dinner** — {inviter.alias} & {invitee.alias}\nThis thread is private. Discuss freely. It will be archived after the Judgement Phase.")
            dinner.status = DinnerStatus.active
            dinner.thread_id = thread.id
            session.commit()
            await interaction.response.send_message("You accepted the dinner invitation.")
            if inviter_member:
                try:
                    await inviter_member.send(f"**{invitee.alias}** accepted your dinner invitation.")
                except Forbidden:
                    pass
        finally:
            session.close()
    

    async def on_decline(self, interaction: Interaction):
        session = get_session()
        try:
            dinner = session.query(Dinner).get(self.dinner_id)
            if dinner.status != DinnerStatus.pending:
                await interaction.response.send_message("This invitation has already been resolved.", ephemeral=True)
                return
            game = session.query(Game).get(dinner.game_id)
            inviter = session.query(Player).get(dinner.inviter_id)
            dinner.status = DinnerStatus.declined
            session.commit()
            await interaction.response.send_message("You declined the dinner invitation.")
            guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
            inviter_member = guild.get_member(inviter.discord_id) or await guild.fetch_member(inviter.discord_id)
            if inviter_member:
                try:
                    await inviter_member.send("Your invitation was rejected.")
                except Forbidden:
                    pass
        finally:
            session.close()


class TipOffTargetView(View):
    def __init__(self, cog, item_id: int, game: Game, options: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.item_id = item_id
        self.game = game
        select = Select(placeholder="Choose a target...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        target_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            item = session.query(Item).get(self.item_id)
            game = session.query(Game).get(self.game.game_id)
            target = session.query(Player).get(target_id)
            target.suspicion = min(100, target.suspicion + 20)
            item.is_used = True
            session.commit()
            guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
            marks = suspicion_checkmarks(target.suspicion)
            await channel.send(embed=Embed(title="📌 Anonymous Tip-off", description=f"**{target.alias}** {marks} has received an anonymous tip-off. Suspicion +20.", color=Color.orange()))
            await interaction.response.send_message("Tip-off sent.", ephemeral=True)
        finally:
            session.close()


class AliasSwapModal(Modal):
    def __init__(self, cog, item_id: int, game: Game, player: Player):
        super().__init__(title="Alias Swap")
        self.cog = cog
        self.item_id = item_id
        self.game = game
        self.player = player
        self.new_alias = TextInput(label="Your new alias", style=TextStyle.short, required=True, min_length=2, max_length=32)
        self.add_item(self.new_alias)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            player = session.query(Player).get(self.player.player_id)
            item = session.query(Item).get(self.item_id)
            new_alias = self.new_alias.value.strip()
            existing = session.query(Player).filter_by(game_id=game.game_id, alias=new_alias).first()
            if existing:
                await interaction.response.send_message("That alias is already taken. Try again.", ephemeral=True)
                return
            player.original_alias = player.alias
            player.alias = new_alias
            item.is_used = True
            session.commit()
            guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
            await channel.send(embed=Embed(title="🎭 Alias Swap", description=f"A player has changed their alias.", color=Color.blurple()))
            await interaction.response.send_message(f"Your alias is now **{new_alias}**.", ephemeral=True)
        finally:
            session.close()


class TelephoneModal(Modal):
    def __init__(self, cog, item_id: int, game: Game, player: Player):
        super().__init__(title="Telephone — Write your message")
        self.cog = cog
        self.item_id = item_id
        self.game = game
        self.player = player
        self.message_input = TextInput(label="Your message", style=TextStyle.paragraph, required=True, max_length=300)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            player = session.query(Player).get(self.player.player_id)
            item = session.query(Item).get(self.item_id)
            active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).order_by(Player.turn_order).all()
            current_pos = next((i for i, j in enumerate(active_players) if j.player_id == player.player_id), None)
            if current_pos is None or len(active_players) < 2:
                await interaction.response.send_message("Not enough active players for Telephone.", ephemeral=True)
                return
            next_player = active_players[(current_pos + 1) % len(active_players)]
            text = self.message_input.value
            telephone = Telephone(game_id=game.game_id, item_id=item.item_id, current_hop=0, original_message=text, current_message=text, next_player_id=next_player.player_id, is_complete=False)
            session.add(telephone)
            item.is_used = True
            session.commit()
            await interaction.response.send_message("Telephone chain started.", ephemeral=True)
            await self.cog.prompt_telephone_hop(game, telephone.telephone_id, session)
        finally:
            session.close()


class TelephoneHopView(View):
    def __init__(self, cog, telephone_id: int, game: Game, player: Player, current_hop: int):
        super().__init__(timeout=game.timeout_hours * 3600)
        self.cog = cog
        self.telephone_id = telephone_id
        self.game = game
        self.player = player
        self.current_hop = current_hop
        btn = Button(label="Forward message", style=ButtonStyle.primary)
        btn.callback = self.on_click
        self.add_item(btn)


    async def on_click(self, interaction: Interaction):
        session = get_session()
        try:
            telephone = session.query(Telephone).get(self.telephone_id)
            if telephone.is_complete:
                await interaction.response.send_message("This telephone chain has already ended.", ephemeral=True)
                return
            await interaction.response.send_modal(TelephoneForwardModal(self.cog, self.telephone_id, self.game, self.player, telephone.current_message, telephone.current_hop))
        finally:
            session.close()


class TelephoneForwardModal(Modal):
    def __init__(self, cog, telephone_id: int, game: Game, player: Player, current_message: str, current_hop: int):
        super().__init__(title=f"Telephone — Hop {current_hop + 1}")
        self.cog = cog
        self.telephone_id = telephone_id
        self.game = game
        self.player = player
        self.current_hop = current_hop
        self.message_input = TextInput(label="Forward this message (you may alter it)", style=TextStyle.paragraph, default=current_message, required=True, max_length=300)
        self.add_item(self.message_input)


    async def on_submit(self, interaction: Interaction):
        session = get_session()
        try:
            telephone = session.query(Telephone).get(self.telephone_id)
            game = session.query(Game).get(self.game.game_id)
            active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).order_by(Player.turn_order).all()
            max_hops = len(active_players)
            telephone.current_message = self.message_input.value
            telephone.current_hop += 1
            session.commit()
            if telephone.current_hop >= max_hops:
                telephone.is_complete = True
                session.commit()
                guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
                channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
                await channel.send(embed=Embed(title="📞 Telephone — Final Message", description=f"*\"{telephone.current_message}\"*", color=Color.teal()))
                await interaction.response.send_message("Message forwarded. Chain completed.", ephemeral=True)
            else:
                current_player = session.query(Player).get(self.player.player_id)
                current_pos = next((i for i, j in enumerate(active_players) if j.player_id == current_player.player_id), None)
                next_player = active_players[(current_pos + 1) % len(active_players)]
                telephone.next_player_id = next_player.player_id
                session.commit()
                await interaction.response.send_message("Message forwarded", ephemeral=True)
                await self.cog.prompt_telephone_hop(game, telephone.telephone_id, session)
        finally:
            session.close()


class WiretapTargetView(View):
    def __init__(self, cog, item_id: int, game: Game, options: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.item_id = item_id
        self.game = game
        select = Select(placeholder="Choose a player to wiretap...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        target_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            item = session.query(Item).get(self.item_id)
            game = session.query(Game).get(self.game.game_id)
            item.is_used = True
            game.wiretap_target_id = target_id
            game.wiretap_owner_id = item.owner_id
            session.commit()
            await interaction.response.send_message("Wiretap activated. You will intercept the next private message or dinner involving that player.", ephemeral=True)
        finally:
            session.close()


class NotebookPageTargetView(View):
    def __init__(self, cog, item_id: int, game: Game, player: Player, options: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.item_id = item_id
        self.game = game
        self.player = player
        select = Select(placeholder="Choose a player to eliminate...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        target_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            item = session.query(Item).get(self.item_id)
            game = session.query(Game).get(self.game.game_id)
            target = session.query(Player).get(target_id)
            target.is_eliminated = True
            item.is_used = True
            session.commit()
            guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
            channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
            await channel.send(embed=Embed(title="📓 Notebook Page", description=f"{target.alias} has been eliminated.\nTheir true identity: **{target.display_name}**", color=Color.dark_red()))
            await interaction.response.send_message("Elimination confirmed.", ephemeral=True)
            justice_cog = self.cog.bot.get_cog("JusticeCog")
            if justice_cog:
                await justice_cog.check_win(game, session, channel, target)
        finally:
            session.close()


class UseItemDMView(View):
    def __init__(self, cog, player: Player, game: Game, item: Item):
        super().__init__(timeout=None)
        self.cog = cog
        self.player = player
        self.game = game
        self.item = item
        btn = Button(label=f"Use {ITEM_LABELS[item.item_type]}", style=ButtonStyle.primary)
        btn.callback = self.on_click
        self.add_item(btn)


    async def on_click(self, interaction: Interaction):
        if not await is_game_active(self.game.game_id):
            await interaction.response.send_message("This game has ended.", ephemeral=True)
            return
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            player = session.query(Player).get(self.player.player_id)
            item = session.query(Item).get(self.item.item_id)
            if item.is_used:
                await interaction.response.send_message("This item has already been used.", ephemeral=True)
                return
            if item.item_type not in (ItemType.alias_swap, ItemType.telephone):
                await interaction.response.defer(ephemeral=True)
            await self.cog.use_item(interaction, item, player, game, session)
        finally:
            session.close()


class DinnerInviteView(View):
    def __init__(self, cog, player: Player, game: Game, options: list):
        super().__init__(timeout=300)
        self.cog = cog
        self.player = player
        self.game = game
        select = Select(placeholder="Choose a player to invite...", options=options)
        select.callback = self.on_select
        self.add_item(select)


    async def on_select(self, interaction: Interaction):
        invitee_id = int(interaction.data["values"][0])
        session = get_session()
        try:
            game = session.query(Game).get(self.game.game_id)
            inviter = session.query(Player).get(self.player.player_id)
            invitee = session.query(Player).filter(Player.game_id == game.game_id, Player.player_id == invitee_id, Player.is_eliminated == False).first()
            if not invitee:
                await interaction.response.send_message("Invalid player.", ephemeral=True)
                return
            dinner = Dinner(game_id=game.game_id, inviter_id=inviter.player_id, invitee_id=invitee.player_id, status=DinnerStatus.pending, round=game.current_round)
            session.add(dinner)
            session.commit()
            await interaction.response.send_message(f"Dinner invitation sent to **{invitee.alias}**.", ephemeral=True)
            guild = self.cog.bot.get_guild(game.guild_id) or await self.cog.bot.fetch_guild(game.guild_id)
            member = guild.get_member(invitee.discord_id) or await guild.fetch_member(invitee.discord_id)
            if member:
                try:
                    await member.send(f"🍽️ **{inviter.alias}** has invited you to dinner. Do you accept?", view=DinnerResponseView(self.cog, dinner.dinner_id, game))
                except Forbidden:
                    pass
        finally:
            session.close()


class ItemsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    

    async def grant_random_item(self, player: Player, game: Game, session, channel):
        rarities = list(RARITY_WEIGHTS.keys())
        weights = list(RARITY_WEIGHTS.values())
        rarity = choices(rarities, weights=weights, k=1)[0]
        item_type = choice(ITEMS_BY_RARITY[rarity])
        item = Item(game_id=game.game_id, owner_id=player.player_id, item_type=item_type, rarity=rarity, is_used=False, obtained_at=utcnow())
        session.add(item)
        session.commit()
        guild = self.bot.get_guild(game.guild_id) or await self.bot.fetch_guild(game.guild_id)
        member = guild.get_member(player.discord_id) or await guild.fetch_member(player.discord_id)
        if member:
            try:
                await member.send(embed=Embed(title=f"🔍 Evidence Found — {RARITY_LABELS[rarity]}", description=f"**{ITEM_LABELS[item_type]}**\n{ITEM_DESCRIPTIONS[item_type]}", color=Color.gold()), view=UseItemDMView(self, player, game, item))
            except Forbidden:
                pass
        await channel.send(embed=Embed(title="🔍 Evidence found.", description="A player found evidence while searching.", color=Color.gold()))


    async def use_item(self, interaction: Interaction, item: Item, player: Player, game: Game, session):
        guild = self.bot.get_guild(game.guild_id) or await self.bot.fetch_guild(game.guild_id)
        if item.item_type == ItemType.tip_off:
            active_others = session.query(Player).filter(Player.game_id == game.game_id, Player.is_eliminated == False, Player.player_id != player.player_id).all()
            options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_others]
            await interaction.followup.send("Choose a target for your tip-off:", view=TipOffTargetView(self, item.item_id, game, options), ephemeral=True)
        elif item.item_type == ItemType.red_herring:
            player.suspicion = min(100, player.suspicion + 30)
            game.red_herring_player_id = player.player_id
            item.is_used = True
            session.commit()
            channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
            await channel.send(embed=Embed(title="🐟 Red Herring", description="A player has planted a Red Herring. Someone's Suspicion rose by 30.", color=Color.orange()))
            await interaction.followup.send("Red Herring planted.", ephemeral=True)
        elif item.item_type == ItemType.alias_swap:
            await interaction.response.send_modal(AliasSwapModal(self, item.item_id, game, player))
        elif item.item_type == ItemType.telephone:
            await interaction.response.send_modal(TelephoneModal(self, item.item_id, game, player))
        elif item.item_type == ItemType.wiretap:
            active_others = session.query(Player).filter(Player.game_id == game.game_id, Player.is_eliminated == False, Player.player_id != player.player_id).all()
            options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_others]
            await interaction.followup.send("Choose a player to wiretap:", view=WiretapTargetView(self, item.item_id, game, options), ephemeral=True)
        elif item.item_type == ItemType.notebook_page:
            active_others = session.query(Player).filter(Player.game_id == game.game_id, Player.is_eliminated == False, Player.player_id != player.player_id).all()
            options = [SelectOption(label=i.alias, value=str(i.player_id)) for i in active_others]
            await interaction.followup.send("Choose a player to eliminate:", view=NotebookPageTargetView(self, item.item_id, game, player, options), ephemeral=True)


    async def prompt_telephone_hop(self, game: Game, telephone_id: int, session):
        telephone = session.query(Telephone).get(telephone_id)
        next_player = session.query(Player).get(telephone.next_player_id)
        active_players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).order_by(Player.turn_order).all()
        max_hops = len(active_players)
        guild = self.bot.get_guild(game.guild_id) or await self.bot.fetch_guild(game.guild_id)
        member = guild.get_member(next_player.discord_id) or await guild.fetch_member(next_player.discord_id)
        if member:
            try:
                await member.send(f"📞 **Telephone** — Hop {telephone.current_hop + 1}/{max_hops}\nYou received a message. Forward it (you may alter it).", view=TelephoneHopView(self, telephone_id, game, next_player, telephone.current_hop))
            except Forbidden:
                telephone.current_hop += 1
                if telephone.current_hop >= max_hops:
                    telephone.is_complete = True
                    session.commit()
                    channel = guild.get_channel(game.channel_id) or await guild.fetch_channel(game.channel_id)
                    await channel.send(embed=Embed(title="📞 Telephone — Final Message", description=f"*\"{telephone.current_message}\"*", color=Color.teal()))
                else:
                    current_pos = next((i for i, j in enumerate(active_players) if j.player_id == next_player.player_id), None)
                    if current_pos is not None:
                        next_next = active_players[(current_pos + 1) % len(active_players)]
                        telephone.next_player_id = next_next.player_id
                    session.commit()
                    await self.prompt_telephone_hop(game, telephone_id, session)


    async def archive_dinners(self, game: Game, session):
        dinners = session.query(Dinner).filter_by(game_id=game.game_id, round=game.current_round, status=DinnerStatus.active).all()
        guild = self.bot.get_guild(game.guild_id) or await self.bot.fetch_guild(game.guild_id)
        for i in dinners:
            if i.thread_id:
                try:
                    thread = guild.get_thread(i.thread_id) or await guild.fetch_channel(i.thread_id)
                    await thread.edit(archived=True)
                except Exception:
                    pass
            i.status = DinnerStatus.archived
        session.commit()


    async def reset_round(self, game: Game, session):
        players = session.query(Player).filter_by(game_id=game.game_id, is_eliminated=False).all()
        for i in players:
            i.dinner_used = False
            if i.original_alias:
                i.alias = i.original_alias
                i.original_alias = None
        session.commit()


async def setup(bot):
    await bot.add_cog(ItemsCog(bot))

