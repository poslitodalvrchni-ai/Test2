import os
import discord
from discord.ext import commands
import random

# Načtení tokenu z proměnné prostředí DISCORD_TOKEN
# Render MUSÍ mít nastavenou proměnnou prostředí DISCORD_TOKEN.
TOKEN = os.getenv('DISCORD_TOKEN')

# Nastavení prefixu a inicializace bota
# Povolujeme Intents, aby Discord povolil čtení obsahu zpráv
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Proměnné pro hru
current_number = None
is_game_active = False

@bot.event
async def on_ready():
    """Zavoláno, když se bot úspěšně připojí."""
    print(f'{bot.user.name} se připojil k Discordu!')
    await bot.change_presence(activity=discord.Game(name="Hádám číslo! (!start)"))

@bot.command(name='start', help='Spustí novou hru hádání čísel. Bot vybere číslo mezi 1 a 100.')
async def start_game(ctx):
    """Spustí novou hru."""
    global current_number, is_game_active
    
    if is_game_active:
        await ctx.send("Hra už běží! Použijte `!guess <číslo>`.")
        return

    current_number = random.randint(1, 100)
    is_game_active = True
    print(f"Nová hra zahájena, číslo je {current_number}")
    await ctx.send(f'Ahoj, {ctx.author.display_name}! Spustil jsem novou hru. Hádám číslo mezi 1 a 100. Začněte s hádáním pomocí `!guess <číslo>`!')

@bot.command(name='guess', help='Zkusí uhodnout číslo.')
async def guess_number(ctx, guess: int):
    """Zpracuje pokus o uhodnutí čísla."""
    global current_number, is_game_active

    if not is_game_active:
        await ctx.send("Žádná aktivní hra. Spusťte novou pomocí `!start`.")
        return

    if not 1 <= guess <= 100:
        await ctx.send("Prosím, hádejte číslo v rozsahu 1 až 100.")
        return
    
    if guess < current_number:
        await ctx.send(f"Příliš malé! Zkuste vyšší číslo, {ctx.author.display_name}.")
    elif guess > current_number:
        await ctx.send(f"Příliš velké! Zkuste nižší číslo, {ctx.author.display_name}.")
    else:
        await ctx.send(f"🎉 **Gratuluji, {ctx.author.display_name}!** Uhodli jste číslo **{current_number}**!")
        is_game_active = False
        current_number = None
        await ctx.send("Hra skončila. Pro novou hru použijte `!start`.")

@bot.command(name='stop', help='Ukončí aktuální hru.')
async def stop_game(ctx):
    """Ukončí aktuální hru."""
    global is_game_active, current_number

    if not is_game_active:
        await ctx.send("Žádná aktivní hra k ukončení.")
        return
    
    is_game_active = False
    current_number = None
    await ctx.send("Aktuální hra byla ukončena.")

# Spuštění bota
if TOKEN:
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Chyba při spuštění bota: {e}")
        print("Ujistěte se, že váš Discord token je správný a má požadovaná oprávnění.")
else:
    print("CHYBA: Discord token nebyl nalezen v proměnných prostředí. Nastavte proměnnou DISCORD_TOKEN v Renderu.")