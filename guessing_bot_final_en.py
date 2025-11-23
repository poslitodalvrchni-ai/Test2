import os
import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import threading
import sys
from flask import Flask # Import Flask for the keep-alive server

# --- FLASK (WEB SERVICE / KEEP-ALIVE) SETUP ---
# Initializes the Flask app
app = Flask(__name__)
# Get the port from environment variables (Render sets this)
WEB_PORT = os.getenv('PORT', 8080) 

@app.route('/')
def home():
    """Simple Health Check endpoint required by Render for Web Services."""
    # Změněno na češtinu pro lepší kontext při kontrole
    return "Item Guessing Bot Worker is Running! (Keep-Alive Active)", 200

def run_flask_app():
    """Starts Flask on a separate thread to listen for web requests (Keep-Alive)."""
    try:
        # Use 0.0.0.0 to listen on all interfaces
        app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
    except Exception as e:
        print(f"Error starting Flask server: {e}", file=sys.stderr)

# --- DISCORD BOT & GAME CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
DATA_FILE = 'user_wins.json'

# --- Custom Restriction IDs ---
# ID hlavní herní kategorie
TARGET_CATEGORY_ID = 1441691009993146490 
# ID kanálu pro žebříček, kde funguje jen !wins
WINS_CHANNEL_ID = 1442057049805422693 
# ID kanálu pro hlášení vítěze
WINNER_ANNOUNCEMENT_CHANNEL_ID = 1441858034291708059
# ID KANÁLU PRO AUTOMATICKÉ ODESÍLÁNÍ NÁPOVĚD
HINT_ANNOUNCEMENT_CHANNEL_ID_PERIODIC = 1441386236844572834 
ADMIN_ROLE_IDS = [
    1397641683205624009, 
    1441386642332979200
]
# Seznam ID rolí, které mají být pingnuty při každé nové nápovědě
HINT_PING_ROLE_IDS = [
    1442080434073895022  # Jediná správná role pro nové nápovědy
]
# ID role, která má být pingnuta po skončení hry
GAME_END_PING_ROLE_ID = 1442080784570646629 

# Set up Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# Game Variables
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
is_game_active = False
hint_timing_minutes = 5
last_hint_reveal_time = None
REQUIRED_HINTS = 7 # Změněno z 5 na 7

# --- Role Configuration (Your Reward IDs) ---
WINNER_ROLES_CONFIG = {
    1:    1441693698776764486,
    5:    1441693984266129469,
    10:   1441694043477381150,
    25:   1441694109268967505,
    50:   1441694179011989534,
    100:  1441694438345674855
}
user_wins = {}

# --- Custom Admin Check ---

def is_authorized_admin():
    """Custom check to ensure the user has one of the specific admin roles."""
    async def predicate(ctx):
        if not ctx.guild:
            return False 
        
        member_roles = [role.id for role in ctx.author.roles]
        
        for required_id in ADMIN_ROLE_IDS:
            if required_id in member_roles:
                return True
                
        return False
    return commands.check(predicate)

# --- Global Command Location Check ---

@bot.check
async def command_location_check(ctx):
    """Global check to restrict commands based on context."""
    if ctx.guild is None:
        return True # Povolit DMs

    # Check 1: Command je v hlavní herní kategorii (Většina příkazů funguje zde)
    if ctx.channel.category_id == TARGET_CATEGORY_ID:
        return True

    # Check 2: Command je ve speciálním kanálu pro žebříček (!wins povolen, ostatní blokovány)
    if ctx.channel.id == WINS_CHANNEL_ID:
        if ctx.command.name in ['wins', 'lbc', 'top']:
            return True # !wins je povolen
        else:
            # Blokovat všechny ostatní příkazy (!guess, !start, atd.)
            await ctx.send("Tento kanál je určen pouze pro žebříček (`!wins`). Hádání a ovládání probíhá v herní kategorii.", delete_after=10)
            return False
    
    # Check 3: Příkaz je v jakémkoli jiném kanálu nebo kategorii
    await ctx.send(f"❌ Tento příkaz lze použít pouze v herní kategorii.", delete_after=10)
    return False

# --- Data Persistence Functions ---
def load_user_wins():
    global user_wins
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                user_wins = {int(k): v for k, v in data.items()}
                print(f"Loaded {len(user_wins)} win records.")
        except json.JSONDecodeError:
            print("ERROR: user_wins.json is corrupted or empty. Starting with empty data.")
            user_wins = {}
    else:
        user_wins = {}

def save_user_wins():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(user_wins, f, indent=4)
            print("Win data saved.")
    except Exception as e:
        print(f"ERROR SAVING DATA: {e}")


# --- Timed Hint Task ---
@tasks.loop(minutes=1)
async def hint_timer():
    global current_hints_revealed, last_hint_reveal_time, current_hints_storage, hint_timing_minutes
    
    if not is_game_active or not last_hint_reveal_time or not current_hints_storage:
        return
        
    now = datetime.now()
    next_reveal_time = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    
    if now >= next_reveal_time:
        next_hint_number = len(current_hints_revealed) + 1
        
        if next_hint_number in current_hints_storage:
            # POUŽIJEME DEDIKOVANÝ KANÁL PRO AUTOMATICKÉ NÁPOVĚDY
            channel = bot.get_channel(HINT_ANNOUNCEMENT_CHANNEL_ID_PERIODIC)
            
            if channel:
                hint_text = current_hints_storage[next_hint_number]
                
                # Vytvoření pingovacího řetězce pro všechny definované role
                ping_string = "".join([f"<@&{role_id}> " for role_id in HINT_PING_ROLE_IDS])
                
                # Sestavíme zprávu, která obsahuje ping na role
                ping_message = f"{ping_string}📢 **Nová Nápověda ({next_hint_number}/{REQUIRED_HINTS}):** {hint_text}"

                await channel.send(ping_message)
                
                # Uložíme pouze číslo a text, ID kanálu už nepotřebujeme
                current_hints_revealed.append({'hint_number': next_hint_number, 'text': hint_text}) 
                last_hint_reveal_time = now
        
        else:
            # All hints revealed, stop the timer
            if hint_timer.is_running():
                hint_timer.stop()
                
# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    load_user_wins()
    await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
    if not hint_timer.is_running():
        hint_timer.start()

# --- Utility Functions ---
async def award_winner_roles(member: discord.Member):
    global user_wins

    user_id = member.id
    guild = member.guild
    
    user_wins[user_id] = user_wins.get(user_id, 0) + 1
    wins_count = user_wins[user_id]
    save_user_wins()

    await member.send(f"Congratulations! You now have {wins_count} wins!")

    achieved_role_id = None
    sorted_wins_levels = sorted(WINNER_ROLES_CONFIG.keys(), reverse=True)
    
    for level in sorted_wins_levels:
        if wins_count >= level:
            achieved_role_id = WINNER_ROLES_CONFIG[level]
            break

    if achieved_role_id:
        target_role = guild.get_role(achieved_role_id)
        
        if not target_role:
            print(f"Role with ID {achieved_role_id} not found.")
            return

        all_winner_role_ids = list(WINNER_ROLES_CONFIG.values())
        
        roles_to_remove = [
            role for role in member.roles 
            if role.id in all_winner_role_ids and role.id != achieved_role_id
        ]

        try:
            if target_role not in member.roles:
                await member.add_roles(target_role)
                await member.send(f"You've reached {wins_count} wins and earned the role **{target_role.name}**!")
            
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
                
        except discord.Forbidden:
            print(f"Permission Error: Cannot add/remove role for {member.display_name}. Check bot permissions and role hierarchy.")
        except Exception as e:
            print(f"Error managing role: {e}")


# --- Admin Commands ---

@bot.command(name='setitem', help='[ADMIN] Sets the correct item name for the game.')
@is_authorized_admin()
async def set_item_name(ctx, *, item_name: str):
    global correct_answer, is_game_active
    
    if is_game_active:
        await ctx.send("Cannot change the item while a game is running.")
        return

    correct_answer = item_name.strip()
    await ctx.send(f"✅ Correct item set to: **{correct_answer}**.")
    await bot.change_presence(activity=discord.Game(name=f"Waiting for hints (!sethint)"))


@bot.command(name='sethint', help=f'[ADMIN] Sets hints 1 through {REQUIRED_HINTS}. Usage: !sethint 1 This is the first hint...')
@is_authorized_admin()
async def set_hint(ctx, number: int, *, hint_text: str):
    global is_game_active, current_hints_storage

    if is_game_active:
        await ctx.send("Cannot modify hints while a game is running.")
        return
    
    # Změněno z 5 na REQUIRED_HINTS (7)
    if not 1 <= number <= REQUIRED_HINTS: 
        await ctx.send(f"❌ Hint number must be between 1 and {REQUIRED_HINTS}.")
        return

    current_hints_storage[number] = hint_text.strip()
    
    current_count = len(current_hints_storage)
    
    # Oznámí aktuální počet nastavených nápověd
    if current_count == REQUIRED_HINTS:
        await ctx.send(f"✅ Hint No. **{number}/{REQUIRED_HINTS}** has been set. **Všech {REQUIRED_HINTS} nápověd je nyní nakonfigurováno!**")
        if correct_answer:
            await bot.change_presence(activity=discord.Game(name=f"Ready! (!start)"))
    else:
        await ctx.send(f"✅ Hint No. **{number}/{REQUIRED_HINTS}** has been set. Aktuálně nakonfigurovaných nápověd: **{current_count}/{REQUIRED_HINTS}**.")


@bot.command(name='sethinttiming', help='[ADMIN] Sets the interval for revealing hints (in minutes).')
@is_authorized_admin()
async def set_hint_timing(ctx, minutes: int):
    global hint_timing_minutes

    if is_game_active:
        await ctx.send("Cannot change timing while a game is running.")
        return

    if minutes < 1 or minutes > 60:
        await ctx.send("Interval must be between 1 and 60 minutes.")
        return
    
    hint_timing_minutes = minutes
    await ctx.send(f"✅ Hint revealing interval set to **{minutes} minutes**.")


@bot.command(name='stop', help='[ADMIN] Ends the current game and clears settings.')
@is_authorized_admin()
async def stop_game(ctx):
    global is_game_active, correct_answer, current_hints_revealed, current_hints_storage

    if not is_game_active:
        await ctx.send("No active game to stop.")
        return
    
    is_game_active = False
    correct_answer = None
    current_hints_revealed = []
    current_hints_storage = {}
    
    if hint_timer.is_running():
        hint_timer.stop()
        
    await ctx.send("The current game has been stopped and item settings cleared. You can set up a new game.")
    await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))

# --- Game Commands ---
@bot.command(name='start', help='Starts a new game with the configured item.')
async def start_game(ctx):
    global correct_answer, is_game_active, current_hints_revealed, last_hint_reveal_time
    
    if is_game_active:
        await ctx.send("A game is already running! Try guessing with `!guess <item>`.")
        return

    # Změněno z 5 na REQUIRED_HINTS (7)
    if not correct_answer or len(current_hints_storage) != REQUIRED_HINTS: 
        await ctx.send(f"❌ The administrator must first set the item and all {REQUIRED_HINTS} hints using `!setitem` and `!sethint <1-{REQUIRED_HINTS}> ...`")
        return

    is_game_active = True
    current_hints_revealed = []
    
    first_hint_text = current_hints_storage[1]
    last_hint_reveal_time = datetime.now()
    
    # Přesunuto na dedikovaný kanál pro nápovědy
    announcement_channel = bot.get_channel(HINT_ANNOUNCEMENT_CHANNEL_ID_PERIODIC)

    if not announcement_channel:
        is_game_active = False # Zrušit spuštění hry
        await ctx.send("❌ Chyba: Kanál pro automatické nápovědy nebyl nalezen. Zkontrolujte ID.")
        return

    # Store the first revealed hint (only number and text, channel ID is no longer needed in the list)
    current_hints_revealed.append({'hint_number': 1, 'text': first_hint_text})

    print(f"New game started, item is {correct_answer}")
    await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))
    
    # Sestavíme zprávu pro první nápovědu (bez pingu, aby se zabránilo spamování hned na začátku)
    start_message = (
        f'A new item guessing game has started. Hints will be revealed every **{hint_timing_minutes} minutes**.'
        f'\n\n**First Hint (1/{REQUIRED_HINTS}):** {first_hint_text}'
        f'\n\nStart guessing with `!guess <item name>`! (Remember the one guess per hour limit.)'
    )
    
    # Odeslání první nápovědy do dedikovaného kanálu
    await announcement_channel.send(start_message)

    # Oznámení pro admina/volajícího, že hra byla spuštěna a kam nápověda šla
    await ctx.send(f"✅ Hra byla spuštěna! První nápověda byla odeslána do kanálu {announcement_channel.mention}.")

# Dictionary to track last guess time for cooldown
last_guess_time = {} 

@bot.command(name='guess', help='Attempts to guess the item name.')
async def guess_item(ctx, *, guess: str):
    global correct_answer, is_game_active

    if not is_game_active:
        await ctx.send("No active game. Start a new one with `!start`.")
        return
    
    # Kontrola, zda je příkaz použit v kanálu pro žebříček
    if ctx.channel.id == WINS_CHANNEL_ID:
        # Tuto kontrolu by měl primárně zachytit globální check, ale zde je explicitní blokování !guess v tomto kanálu
        await ctx.send("❌ Hádání (`!guess`) není v tomto kanálu povoleno. Použijte herní kategorii.", delete_after=10)
        return

    user_id = ctx.author.id
    now = datetime.now()
    cooldown_minutes = 60 # One hour
    
    # Check cooldown
    if user_id in last_guess_time:
        time_since_last_guess = now - last_guess_time[user_id]
        if time_since_last_guess < timedelta(minutes=cooldown_minutes):
            remaining_time = timedelta(minutes=cooldown_minutes) - time_since_last_guess
            seconds = int(remaining_time.total_seconds())
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            
            await ctx.send(f"🛑 **Cooldown Active:** You must wait **{hours}h {minutes}m** before guessing again.", delete_after=5)
            return

    # Record the new guess time *before* checking accuracy
    last_guess_time[user_id] = now
    
    # Check the guess (case-insensitive)
    if guess.strip().lower() == correct_answer.lower():
        # 1. Oznámení ve stávajícím kanále
        await ctx.send(f"🎉 **Congratulations, {ctx.author.display_name}!** You guessed the item: **{correct_answer}**! The game is over!")

        # 2. Oznámení v dedikovaném kanále s pingem
        announcement_channel = bot.get_channel(WINNER_ANNOUNCEMENT_CHANNEL_ID)
        if announcement_channel:
            winner_ping = ctx.author.mention
            message = f"🏆 **VÍTĚZ KOLA!** {winner_ping} právě uhodl předmět. Správná odpověď byla: **{correct_answer}**!"
            await announcement_channel.send(message)
        
        if hint_timer.is_running():
            hint_timer.stop()
        
        await award_winner_roles(ctx.author)

        is_game_active = False
        correct_answer = None # Clear item for next round
        current_hints_revealed = []
        current_hints_storage = {}
        
        # Ping role při konci hry (adminům pro nastavení další hry)
        game_end_ping_string = f"<@&{GAME_END_PING_ROLE_ID}>"
        await ctx.send(f"{game_end_ping_string} ✅ Hra skončila a správce může nastavit další kolo pomocí `!setitem`.")

    else:
        await ctx.send(f"❌ Wrong! **{ctx.author.display_name}**, that's not it. You can guess again in 60 minutes.")

# --- Leaderboard Command ---

@bot.command(name='wins', aliases=['lbc', 'top'], help='Displays the top 10 winners.')
async def show_leaderboard(ctx):
    """Displays the winners leaderboard and shows user's own win count."""
    
    user_id = ctx.author.id
    user_wins_count = user_wins.get(user_id, 0)

    if not user_wins:
        await ctx.send(f"Nikdo zatím nevyhrál! Buďte první, kdo uhodne. (Vaše výhry: 0)")
        return

    sorted_winners = sorted(user_wins.items(), key=lambda item: item[1], reverse=True)
    
    leaderboard_embed = discord.Embed(
        title="🏆 Item Guessing Leaderboard",
        description=f"Top 10 uživatelů s nejvíce uhodnutými předměty.\n\n**Vaše celkové výhry:** {user_wins_count}",
        color=discord.Color.gold()
    )
    
    rank = 1
    for user_id, wins in sorted_winners[:10]:
        member = ctx.guild.get_member(user_id)
        member_name = member.display_name if member else f"Neznámý Uživatel ({user_id})"
        
        leaderboard_embed.add_field(
            name=f"#{rank}. {member_name}",
            value=f"**{wins}** výher",
            inline=False
        )
        rank += 1

    await ctx.send(embed=leaderboard_embed)

# --- BOT STARTUP ---
if TOKEN:
    # 1. Start the Flask server in a separate thread (KEEP-ALIVE)
    try:
        flask_thread = threading.Thread(target=run_flask_app, daemon=True)
        flask_thread.start()
        print(f"Flask server started on port: {WEB_PORT}")
    except Exception as e:
        print(f"ERROR: Could not start Flask thread: {e}")
        
    # 2. Start the Discord bot in the main thread
    try:
        bot.run(TOKEN, reconnect=True)
    except Exception as e:
        print(f"Error running the bot: {e}")
else:
    print("ERROR: Discord token not found in environment variables. Cannot start bot.")
