import os
import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import threading
import sys
from flask import Flask

# --- FLASK (WEB SERVICE) SETUP ---
# Initializes the Flask app
app = Flask(__name__)
WEB_PORT = os.getenv('PORT', 8080) 

@app.route('/')
def home():
    """Simple Health Check endpoint required by Render for Web Services."""
    return "Item Guessing Bot Worker is Running!", 200

def run_flask_app():
    """Starts Flask on a separate thread to listen for web requests."""
    try:
        app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
    except Exception as e:
        print(f"Error starting Flask server: {e}", file=sys.stderr)

# --- DISCORD BOT & GAME CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
DATA_FILE = 'user_wins.json'

# --- Custom Restriction IDs ---
TARGET_CATEGORY_ID = 1441691009993146490
ADMIN_ROLE_IDS = [
    1397641683205624009,  # První ID role
    1441386642332979200   # Druhé ID role
]

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
            # Admin commands should not work in DMs
            return False 
        
        member_roles = [role.id for role in ctx.author.roles]
        
        # Check if the member has any of the required admin roles
        for required_id in ADMIN_ROLE_IDS:
            if required_id in member_roles:
                return True
                
        return False
    return commands.check(predicate)

# --- Global Category Check ---

@bot.check
async def category_check(ctx):
    """Global check to restrict all bot commands to the TARGET_CATEGORY_ID."""
    # Allow commands from DMs (although !start, !guess, etc. won't fully function without a guild)
    if ctx.guild is None:
        return True
        
    if ctx.channel.category_id == TARGET_CATEGORY_ID:
        return True
    
    # Optionally send a message to the user/channel if the command is used outside the designated category
    # await ctx.send("This bot's commands can only be used in the designated category.", delete_after=5)
    return False

# --- Data Persistence Functions ---
# (load_user_wins and save_user_wins remain unchanged)
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
# (hint_timer remains unchanged)
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
            channel = bot.get_channel(current_hints_revealed[0]['channel_id'])
            
            if channel:
                hint_text = current_hints_storage[next_hint_number]
                await channel.send(f"⏳ **New Hint ({next_hint_number}/{len(current_hints_storage)}):** {hint_text}")
                
                current_hints_revealed.append({'hint_number': next_hint_number, 'text': hint_text, 'channel_id': channel.id})
                last_hint_reveal_time = now
        
        else:
            if hint_timer.is_running():
                hint_timer.stop()
                
# --- Bot Events ---
# (on_ready remains unchanged)
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    load_user_wins()
    await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
    if not hint_timer.is_running():
        hint_timer.start()

# --- Utility Functions ---
# (award_winner_roles remains unchanged)
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
                await member.send(f"You're amazing! You've reached {wins_count} wins and earned the role **{target_role.name}**!")
            
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
                
        except discord.Forbidden:
            print(f"Permission Error: Cannot add/remove role for {member.display_name}. Check bot permissions and role hierarchy.")
        except Exception as e:
            print(f"Error managing role: {e}")


# --- Admin Commands (Now using Custom Check) ---

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


@bot.command(name='sethint', help='[ADMIN] Sets hints 1 through 5. Usage: !sethint 1 This is the first hint...')
@is_authorized_admin()
async def set_hint(ctx, number: int, *, hint_text: str):
    global is_game_active, current_hints_storage

    if is_game_active:
        await ctx.send("Cannot modify hints while a game is running.")
        return
        
    if not 1 <= number <= 5:
        await ctx.send("❌ Hint number must be between 1 and 5.")
        return

    current_hints_storage[number] = hint_text.strip()
    await ctx.send(f"✅ Hint No. **{number}/5** has been set.")
    
    if correct_answer and len(current_hints_storage) == 5:
        await bot.change_presence(activity=discord.Game(name=f"Ready! (!start)"))


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

# --- Game Commands (Unchanged) ---
@bot.command(name='start', help='Starts a new game with the configured item.')
async def start_game(ctx):
    global correct_answer, is_game_active, current_hints_revealed, last_hint_reveal_time
    
    if is_game_active:
        await ctx.send("A game is already running! Try guessing with `!guess <item>`.")
        return

    if not correct_answer or len(current_hints_storage) != 5:
        await ctx.send(f"❌ The administrator must first set the item and all 5 hints using `!setitem` and `!sethint <1-5> ...`")
        return

    is_game_active = True
    current_hints_revealed = []
    
    first_hint_text = current_hints_storage[1]
    last_hint_reveal_time = datetime.now()
    
    current_hints_revealed.append({'hint_number': 1, 'text': first_hint_text, 'channel_id': ctx.channel.id})

    print(f"New game started, item is {correct_answer}")
    await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))
    await ctx.send(
        f'Hello, **{ctx.author.display_name}**! A new item guessing game has started. '
        f'Hints will be revealed every **{hint_timing_minutes} minutes**.'
        f'\n\n**First Hint (1/5):** {first_hint_text}'
        f'\n\nStart guessing with `!guess <item name>`!'
    )

@bot.command(name='guess', help='Attempts to guess the item name.')
async def guess_item(ctx, *, guess: str):
    global correct_answer, is_game_active

    if not is_game_active:
        await ctx.send("No active game. Start a new one with `!start`.")
        return
    
    if guess.strip().lower() == correct_answer.lower():
        await ctx.send(f"🎉 **Congratulations, {ctx.author.display_name}!** You guessed the item: **{correct_answer}**!")
        
        if hint_timer.is_running():
            hint_timer.stop()
        
        await award_winner_roles(ctx.author)

        is_game_active = False
        await ctx.send("Game over. Use `!setitem` to set up the next item, then `!start`.")
    else:
        await ctx.send(f"❌ Wrong! **{ctx.author.display_name}**, try again. Check the hints!")

# --- Leaderboard Command (Renamed) ---

@bot.command(name='wins', aliases=['lbc', 'top'], help='Displays the top 10 winners.')
async def show_leaderboard(ctx):
    """Displays the winners leaderboard."""
    if not user_wins:
        await ctx.send("No one has won yet! Be the first to guess correctly.")
        return

    sorted_winners = sorted(user_wins.items(), key=lambda item: item[1], reverse=True)
    
    leaderboard_embed = discord.Embed(
        title="🏆 Item Guessing Leaderboard",
        description="Top 10 users with the most correctly guessed items.",
        color=discord.Color.gold()
    )
    
    rank = 1
    for user_id, wins in sorted_winners[:10]:
        member = ctx.guild.get_member(user_id)
        member_name = member.display_name if member else f"Unknown User ({user_id})"
        
        leaderboard_embed.add_field(
            name=f"#{rank}. {member_name}",
            value=f"**{wins}** wins",
            inline=False
        )
        rank += 1

    await ctx.send(embed=leaderboard_embed)

# --- BOT STARTUP ---
if TOKEN:
    # 1. Start the Flask server in a separate thread
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
