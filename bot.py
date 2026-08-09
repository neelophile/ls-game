from discord import Intents, Object, Interaction
from dotenv import load_dotenv
from os import getenv
from discord.ext import commands, tasks
from db.database import init_db, get_session
from db.models import Player, Game, Turn, Phase, utcnow
from database import database


load_dotenv()
guild = Object(id=int(getenv("GUILD")))
intents = Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='.', intents=intents)
cogs = ['cogs.game', 'cogs.debate', 'cogs.justice', 'cogs.resolution']


@tasks.loop(minutes=1)
async def timeout_check():
    session = get_session()
    try:
        now = datetime.now(timezone.utc)
        timed_out = session.query(Player).filter(Player.timeout_at != None, Player.timeout_at <= now, Player.is_eliminated == False).all()
        for i in timed_out:
            game = session.query(Game).filter(i.game_id)
            if not game or game.status != GameStatus.active:
                i.timeout_at = None
                session.commit()
                continue
            i.timeout_at = None
            session.commit()
            debate_cog = bot.get_cog("DebateCog")
            if debate_cog:
                await debate_cog.auto_pass(i.game_id, i)
    finally:
        session.close()


async def setup_hook():
    for cog in cogs:
        await bot.load_extension(cog)
    timeout_check.start()


@bot.event
async def on_ready():
    init_db()
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"Logged in as {bot.user}.")


@bot.tree.command(name="ping", description="Provides the latency.")
async def ping(interaction: Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms")


bot.setup_hook = setup_hook
bot.run(getenv("TOKEN"))
