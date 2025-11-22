import os
import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta

# --- Configuration & Setup ---
TOKEN = os.getenv('DISCORD_TOKEN')
DATA_FILE = 'user_wins.json' # Soubor pro trvalé ukládání dat

intents = discord.Intents.default()
intents.message_content = True
# Nutné pro práci s rolemi a získání jmen členů pro leaderboard
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Game Variables ---
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
is_game_active = False
hint_timing_minutes = 5
last_hint_reveal_time = None

# --- Role Configuration (Vaše nastavené ID) ---
WINNER_ROLES_CONFIG = {
    # Kde klíč je minimální počet vítězství potřebný pro získání role
    1:    1441693698776764486,  # 1x vítěz
    5:    1441693984266129469,  # 5x vítěz
    10:   1441694043477381150,  # 10x vítěz
    25:   1441694109268967505,  # 25x vítěz
    50:   1441694179011989534,  # 50x vítěz
    # Používáme ID pro 100+ pro klíč 100, který pokrývá vše od 100 výše.
    100:  1441694438345674855   # 100x a 100+ vítěz
}
user_wins = {} # Bude načteno z JSON

# --- Data Persistence Functions ---

def load_user_wins():
    """Načte data o vítězstvích ze souboru JSON."""
    global user_wins
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                # Převede klíče (user_id) ze stringů na integer
                user_wins = {int(k): v for k, v in data.items()}
                print(f"Načteno {len(user_wins)} záznamů o vítězstvích.")
        except json.JSONDecodeError:
            print("CHYBA: Soubor user_wins.json je poškozený nebo prázdný. Začínám s prázdnými daty.")
            user_wins = {}
    else:
        user_wins = {}

def save_user_wins():
    """Uloží data o vítězstvích do souboru JSON."""
    try:
        with open(DATA_FILE, 'w') as f:
            # JSON klíče musí být stringy, takže int klíče se převedou na string
            json.dump(user_wins, f, indent=4)
            print("Data o vítězstvích uložena.")
    except Exception as e:
        print(f"CHYBA PŘI UKLÁDÁNÍ DAT: {e}")

# --- Timed Hint Task ---

@tasks.loop(minutes=1)
async def hint_timer():
    """Kontroluje, zda je čas na odhalení další nápovědy."""
    global current_hints_revealed, last_hint_reveal_time, current_hints_storage, hint_timing_minutes
    
    if not is_game_active or not last_hint_reveal_time or not current_hints_storage:
        return
        
    now = datetime.now()
    next_reveal_time = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    
    if now >= next_reveal_time:
        next_hint_number = len(current_hints_revealed) + 1
        
        if next_hint_number in current_hints_storage:
            # Kanál je uložen v kontextu první odhalené nápovědy
            channel = bot.get_channel(current_hints_revealed[0]['channel_id'])
            
            if channel:
                hint_text = current_hints_storage[next_hint_number]
                await channel.send(f"⏳ **Nová nápověda ({next_hint_number}/{len(current_hints_storage)}):** {hint_text}")
                
                current_hints_revealed.append({'hint_number': next_hint_number, 'text': hint_text, 'channel_id': channel.id})
                last_hint_reveal_time = now
        
        else:
            # Všechny nápovědy byly odhaleny, zastavíme časovač
            if hint_timer.is_running():
                hint_timer.stop()
                
# --- Bot Events ---

@bot.event
async def on_ready():
    """Zavoláno, když se bot úspěšně připojí."""
    print(f'{bot.user.name} se připojil k Discordu!')
    load_user_wins() # Načtení dat při startu
    await bot.change_presence(activity=discord.Game(name=f"Nastavuji hru (!setitem)"))
    if not hint_timer.is_running():
        hint_timer.start()

# --- Utility Functions ---

async def award_winner_roles(member: discord.Member):
    """Přidělí vítěznou roli na základě počtu vítězství a uloží data."""
    global user_wins

    user_id = member.id
    guild = member.guild
    
    # 1. Aktualizujeme počet vítězství
    user_wins[user_id] = user_wins.get(user_id, 0) + 1
    wins_count = user_wins[user_id]
    
    # 2. ULOŽENÍ dat pro perzistenci
    save_user_wins()

    await member.send(f"Gratuluji! Máš už {wins_count} vítězství!")

    # 3. Určíme nejvyšší roli, kterou uživatel dosáhl
    achieved_role_id = None
    # Seřadíme klíče v opačném pořadí (od 100 dolů), aby se přidělila nejvyšší role
    sorted_wins_levels = sorted(WINNER_ROLES_CONFIG.keys(), reverse=True)
    
    for level in sorted_wins_levels:
        if wins_count >= level:
            achieved_role_id = WINNER_ROLES_CONFIG[level]
            # Jakmile najdeme nejvyšší dosaženou roli, můžeme skončit
            break

    if achieved_role_id:
        target_role = guild.get_role(achieved_role_id)
        
        if not target_role:
            print(f"Role s ID {achieved_role_id} nebyla nalezena. Zkontrolujte ID role.")
            return

        # Vytvoříme seznam ID všech vítězných rolí
        all_winner_role_ids = list(WINNER_ROLES_CONFIG.values())
        
        # Odebereme všechny předchozí vítězné role (pokud je má)
        roles_to_remove = [
            role for role in member.roles 
            if role.id in all_winner_role_ids and role.id != achieved_role_id
        ]

        try:
            # Přidáme/ponecháme cílovou roli (pokud ji již nemá)
            if target_role not in member.roles:
                await member.add_roles(target_role)
                await member.send(f"Jsi úžasný/á! Nyní máš {wins_count} vítězství a získal/a jsi roli **{target_role.name}**!")
            
            # Odebereme staré/nižší role
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
                
        except discord.Forbidden:
            print(f"Chyba oprávnění: Nemohu přidat/odebrat roli uživateli {member.display_name}. Zkontrolujte oprávnění bota a jeho pozici v hierarchii rolí.")
        except Exception as e:
            print(f"Chyba při přidávání role: {e}")


# --- Bot Commands (Admin nastavení) ---

@bot.command(name='setitem', help='[ADMIN] Nastaví správný název předmětu pro hru.')
@commands.has_permissions(administrator=True)
async def set_item_name(ctx, *, item_name: str):
    """Nastaví název předmětu."""
    global correct_answer, is_game_active
    
    if is_game_active:
        await ctx.send("Nelze měnit předmět, dokud běží hra.")
        return

    correct_answer = item_name.strip()
    await ctx.send(f"✅ Správný předmět pro hru byl nastaven na: **{correct_answer}**.")
    await bot.change_presence(activity=discord.Game(name=f"Čekám na nápovědy (!sethint)"))


@bot.command(name='sethint', help='[ADMIN] Nastaví nápovědu 1 až 5. Použití: !sethint 1 Tato nápověda...')
@commands.has_permissions(administrator=True)
async def set_hint(ctx, number: int, *, hint_text: str):
    """Nastaví jednu z pěti nápověd."""
    global is_game_active, current_hints_storage

    if is_game_active:
        await ctx.send("Nelze měnit nápovědy, dokud běží hra.")
        return
        
    if not 1 <= number <= 5:
        await ctx.send("❌ Číslo nápovědy musí být v rozsahu 1 až 5.")
        return

    current_hints_storage[number] = hint_text.strip()
    await ctx.send(f"✅ Nápověda č. **{number}/5** byla nastavena.")
    
    if correct_answer and len(current_hints_storage) == 5:
        await bot.change_presence(activity=discord.Game(name=f"Připraveno! (!start)"))


@bot.command(name='sethinttiming', help='[ADMIN] Nastaví interval pro odhalování nápověd (v minutách).')
@commands.has_permissions(administrator=True)
async def set_hint_timing(ctx, minutes: int):
    """Nastaví interval nápověd."""
    global hint_timing_minutes

    if is_game_active:
        await ctx.send("Nelze měnit časování, zatímco běží hra.")
        return

    if minutes < 1 or minutes > 60:
        await ctx.send("Interval musí být mezi 1 a 60 minutami.")
        return
    
    hint_timing_minutes = minutes
    await ctx.send(f"✅ Interval odhalování nápověd byl nastaven na **{minutes} minut**.")


@bot.command(name='stop', help='[ADMIN] Ukončí aktuální hru a vyčistí nastavení.')
@commands.has_permissions(administrator=True)
async def stop_game(ctx):
    """Ukončí aktuální hru."""
    global is_game_active, correct_answer, current_hints_revealed, current_hints_storage

    if not is_game_active:
        await ctx.send("Žádná aktivní hra k ukončení.")
        return
    
    is_game_active = False
    correct_answer = None
    current_hints_revealed = []
    current_hints_storage = {}
    
    if hint_timer.is_running():
        hint_timer.stop()
        
    await ctx.send("Aktuální hra byla ukončena a nastavení předmětu bylo vymazáno. Můžete nastavit novou hru.")
    await bot.change_presence(activity=discord.Game(name=f"Nastavuji hru (!setitem)"))

# --- Bot Commands (Hra a Leaderboard) ---

@bot.command(name='start', help='Spustí novou hru s nastaveným předmětem.')
async def start_game(ctx):
    """Spustí novou hru."""
    global correct_answer, is_game_active, current_hints_revealed, last_hint_reveal_time
    
    if is_game_active:
        await ctx.send("Hra už běží! Zkuste uhodnout pomocí `!guess <předmět>`.")
        return

    if not correct_answer or len(current_hints_storage) != 5:
        await ctx.send(f"❌ Nejdříve musí administrátor nastavit předmět a všech 5 nápověd pomocí `!setitem` a `!sethint <1-5> ...`")
        return

    is_game_active = True
    current_hints_revealed = []
    
    first_hint_text = current_hints_storage[1]
    last_hint_reveal_time = datetime.now()
    
    current_hints_revealed.append({'hint_number': 1, 'text': first_hint_text, 'channel_id': ctx.channel.id})

    print(f"Nová hra zahájena, předmět je {correct_answer}")
    await bot.change_presence(activity=discord.Game(name=f"Hádá se předmět! (!guess)"))
    await ctx.send(
        f'Ahoj, **{ctx.author.display_name}**! Spustil jsem novou hru. Hádejte název předmětu! '
        f'Nápovědu odhalím každých **{hint_timing_minutes} minut**.'
        f'\n\n**První nápověda (1/5):** {first_hint_text}'
        f'\n\nZačněte s hádáním pomocí `!guess <název předmětu>`!'
    )

@bot.command(name='guess', help='Zkusí uhodnout název předmětu.')
async def guess_item(ctx, *, guess: str):
    """Zpracuje pokus o uhodnutí předmětu."""
    global correct_answer, is_game_active

    if not is_game_active:
        await ctx.send("Žádná aktivní hra. Spusťte novou pomocí `!start`.")
        return
    
    if guess.strip().lower() == correct_answer.lower():
        await ctx.send(f"🎉 **Gratuluji, {ctx.author.display_name}!** Uhodli jste předmět: **{correct_answer}**!")
        
        if hint_timer.is_running():
            hint_timer.stop()
        
        # Uložení vítězství a přidělení role
        await award_winner_roles(ctx.author)

        is_game_active = False
        await ctx.send("Hra skončila. Pro nastavení nového předmětu použijte `!setitem` a pak `!start`.")
    else:
        await ctx.send(f"❌ Špatně! **{ctx.author.display_name}**, zkuste to znovu. Podívejte se na nápovědy!")

@bot.command(name='leaderboard', aliases=['lbc', 'top'], help='Zobrazí žebříček top 10 vítězů.')
async def show_leaderboard(ctx):
    """Zobrazí žebříček vítězů."""
    if not user_wins:
        await ctx.send("Zatím nikdo nevyhrál! Buď první, kdo to uhodne.")
        return

    # Seřadíme uživatele podle počtu vítězství (sestupně)
    sorted_winners = sorted(user_wins.items(), key=lambda item: item[1], reverse=True)
    
    leaderboard_embed = discord.Embed(
        title="🏆 Žebříček nejlepších hádajících",
        description="Top 10 uživatelů s největším počtem uhádnutých předmětů.",
        color=discord.Color.gold()
    )
    
    rank = 1
    for user_id, wins in sorted_winners[:10]:
        # Získáme objekt člena na serveru pro zobrazení jména
        member = ctx.guild.get_member(user_id)
        
        # Pokud člen existuje, použijeme jeho jméno, jinak jen ID
        member_name = member.display_name if member else f"Neznámý uživatel ({user_id})"
        
        leaderboard_embed.add_field(
            name=f"#{rank}. {member_name}",
            value=f"**{wins}** vítězství",
            inline=False
        )
        rank += 1

    await ctx.send(embed=leaderboard_embed)

# --- Spuštění bota ---
if TOKEN:
    try:
        bot.run(TOKEN, reconnect=True)
    except Exception as e:
        print(f"Chyba při spuštění bota: {e}")
else:
    print("CHYBA: Discord token nebyl nalezen v proměnných prostředí. Nastavte proměnnou DISCORD_TOKEN.")
