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

# Render provides the port in the PORT environment variable
WEB_PORT = os.getenv('PORT', 8080) 

@app.route('/')
def home():
    """Simple Health Check endpoint required by Render for Web Services."""
    return "Item Guessing Bot is Running!", 200

def run_flask_app():
    """Starts Flask on a separate thread to listen for web requests."""
    try:
        # Flask is run on 0.0.0.0 to listen on all interfaces, using the port provided by Render
        app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
    except Exception as e:
        print(f"Error starting Flask server: {e}", file=sys.stderr)

# --- DISCORD BOT & GAME CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
DATA_FILE = 'user_wins.json'

# Set up Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Required for fetching member names and managing roles
bot = commands.Bot(command_prefix='!', intents=intents)

# Game Variables
correct_answer = None
current_hints_storage = {}  # Stores {1: 'hint text', ...} set by admin
current_hints_revealed = [] # Stores revealed hints during the active game
is_game_active = False
hint_timing_minutes = 5     # Default hint interval
last_hint_reveal_time = None

# --- Role Configuration (Your IDs used here) ---
WINNER_ROLES_CONFIG = {
    # Key is the minimum win count required for the role
    1:    1441693698776764486,  # 1x Winner
    5:    1441693984266129469,  # 5x Winner
    10:   1441694043477381150,  # 10x Winner
    25:   1441694109268967505,  # 25x Winner
    50:   1441694179011989534,  # 50x Winner
    100:  1441694438345674855   # 100x and 100+ Winner
}
user_wins = {} # Loaded from JSON

# --- Data Persistence Functions ---

def load_user_wins():
    """Loads win data from the JSON file."""
    global user_wins
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                # Convert string keys back to integers for user IDs
                user_wins = {int(k): v for k, v in data.items()}
                print(f"Loaded {len(user_wins)} win records.")
        except json.JSONDecodeError:
            print("ERROR: user_wins.json is corrupted or empty. Starting with empty data.")
            user_wins = {}
    else:
        user_wins = {}

def save_user_wins():
    """Saves win data to the JSON file."""
    try:
        with open(DATA_FILE, 'w') as f:
            # JSON keys must be strings, so convert integer keys to string
            json.dump(user_wins, f, indent=4)
            print("Win data saved.")
    except Exception as e:
        print(f"ERROR SAVING DATA: {e}")

# --- Timed Hint Task ---

@tasks.loop(minutes=1)
async def hint_timer():
    """Checks if it's time to reveal the next hint."""
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

@bot.event
async def on_ready():
    """Called when the bot successfully connects."""
    print(f'{bot.user.name} has connected to Discord!')
    load_user_wins() # Load data on startup
    await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
    if not hint_timer.is_running():
        hint_timer.start()

# --- Utility Functions ---

async def award_winner_roles(member: discord.Member):
    """Awards the highest achieved winner role based on win count and saves data."""
    global user_wins

    user_id = member.id
    guild = member.guild
    
    # 1. Update win count and save
    user_wins[user_id] = user_wins.get(user_id, 0) + 1
    wins_count = user_wins[user_id]
    save_user_wins()

    await member.send(f"Congratulations! You now have {wins_count} wins!")

    # 2. Determine the highest role achieved
    achieved_role_id = None
    # Sort keys in reverse order (100 down) to ensure the highest role is selected
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
        
        # Identify lower roles to remove
        roles_to_remove = [
            role for role in member.roles 
            if role.id in all_winner_role_ids and role.id != achieved_role_id
        ]

        try:
            # Add or keep the target role
            if target_role not in member.roles:
                await member.add_roles(target_role)
                await member.send(f"You're amazing! You've reached {wins_count} wins and earned the role **{target_role.name}**!")
            
            # Remove old/lower roles
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
                
        except discord.Forbidden:
            print(f"Permission Error: Cannot add/remove role for {member.display_name}. Check bot permissions and role hierarchy.")
        except Exception as e:
            print(f"Error managing role: {e}")


# --- Admin Commands ---

@bot.command(name='setitem', help='[ADMIN] Sets the correct item name for the game.')
@commands.has_permissions(administrator=True)
async def set_item_name(ctx, *, item_name: str):
    """Sets the item name."""
    global correct_answer, is_game_active
    
    if is_game_active:
        await ctx.send("Cannot change the item while a game is running.")
        return

    correct_answer = item_name.strip()
    await ctx.send(f"✅ Correct item set to: **{correct_answer}**.")
    await bot.change_presence(activity=discord.Game(name=f"Waiting for hints (!sethint)"))


@bot.command(name='sethint', help='[ADMIN] Sets hints 1 through 5. Usage: !sethint 1 This is the first hint...')
@commands.has_permissions(administrator=True)
async def set_hint(ctx, number: int, *, hint_text: str):
    """Sets one of the five hints."""
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
@commands.has_permissions(administrator=True)
async def set_hint_timing(ctx, minutes: int):
    """Sets the hint interval."""
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
@commands.has_permissions(administrator=True)
async def stop_game(ctx):
    """Ends the current game."""
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

# --- Game and Leaderboard Commands ---

@bot.command(name='start', help='Starts a new game with the configured item.')
async def start_game(ctx):
    """Starts a new game."""
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
    """Processes the item guess."""
    global correct_answer, is_game_active

    if not is_game_active:
        await ctx.send("No active game. Start a new one with `!start`.")
        return
    
    if guess.strip().lower() == correct_answer.lower():
        await ctx.send(f"🎉 **Congratulations, {ctx.author.display_name}!** You guessed the item: **{correct_answer}**!")
        
        if hint_timer.is_running():
            hint_timer.stop()
        
        # Save win and award role
        await award_winner_roles(ctx.author)

        is_game_active = False
        await ctx.send("Game over. Use `!setitem` to set up the next item, then `!start`.")
    else:
        await ctx.send(f"❌ Wrong! **{ctx.author.display_name}**, try again. Check the hints!")

@bot.command(name='leaderboard', aliases=['lbc', 'top'], help='Displays the top 10 winners.')
async def show_leaderboard(ctx):
    """Displays the winners leaderboard."""
    if not user_wins:
        await ctx.send("No one has won yet! Be the first to guess correctly.")
        return

    # Sort users by win count (descending)
    sorted_winners = sorted(user_wins.items(), key=lambda item: item[1], reverse=True)
    
    leaderboard_embed = discord.Embed(
        title="🏆 Item Guessing Leaderboard",
        description="Top 10 users with the most correctly guessed items.",
        color=discord.Color.gold()
    )
    
    rank = 1
    for user_id, wins in sorted_winners[:10]:
        # Get member object for display name
        member = ctx.guild.get_member(user_id)
        
        # Use display name if member exists, otherwise use ID
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
        # daemon=True allows the thread to be cleanly terminated when the main program exits
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
