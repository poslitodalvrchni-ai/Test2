import os
import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import threading
import concurrent.futures # Needed for running sync file I/O asynchronously
import sys
from flask import Flask

# --- FLASK (WEB SERVICE / KEEP-ALIVE) SETUP ---
app = Flask(__name__)
WEB_PORT = os.getenv('PORT', 8080)

@app.route('/')
def home():
    """Simple Health Check endpoint required by Render for Web Services."""
    return "Item Guessing Bot Worker is Running! (Keep-Alive Active)", 200

def run_flask():
    """Runs the Flask server in a separate thread."""
    print(f"Starting Flask keep-alive server on port {WEB_PORT}...")
    app.run(host='0.0.0.0', port=WEB_PORT)

# --- BOT CONFIGURATION AND CONSTANTS ---
MAX_GAME_QUEUE_SIZE = 5

CONFIG = {
    # File Persistence
    'DATA_FILE': 'user_wins.json',
    'GAME_STATE_FILE': 'game_state_queue.json', # New file name for queue
    
    # Game Parameters
    'REQUIRED_HINTS': 7,
    'GUESS_COOLDOWN_MINUTES': 30,
    'DEFAULT_HINT_TIMING_MINUTES': 60,

    # Channel and Category IDs (***UPDATE THESE PLACEHOLDERS***)
    'TARGET_CATEGORY_ID': 1441691009993146490,
    'WINS_CHANNEL_ID': 1442057049805422693,
    'WINNER_ANNOUNCEMENT_CHANNEL_ID': 1441858034291708059,
    'HINT_CHANNEL_ID': 1441386236844572834,
    
    # Role IDs
    'ADMIN_ROLE_IDS': [
        1397641683205624009,
        1441386642332979200
    ],
    'HINT_PING_ROLE_IDS': [
        1441388270201077882
    ],
    'GAME_END_PING_ROLE_ID': 1441386642332979200,

    # Winner Roles
    'WINNER_ROLES_CONFIG': {
        1:      1441693698776764486,
        5:      1441693984266129469,
        10:     1441694043477381150,
        25:     1441694109268967505,
        50:     1441694179011989534,
        100:    1441694438345674855
    }
}

# --- Game State Variables (Refactored to Queue) ---
game_queue = [] # Stores up to 5 game dictionaries
# Dictionary to track last guess time for cooldown
last_guess_time = {}
user_wins = {}

# Template for a single game object
GAME_TEMPLATE = {
    'item_name': None,
    'hints_storage': {}, # {1: 'hint text', 2: 'hint text'}
    'hints_revealed': [], # [{'hint_number': 1, 'text': '...'}, ...]
    'hint_timing_minutes': CONFIG['DEFAULT_HINT_TIMING_MINUTES'],
    'last_hint_reveal_time_iso': None, # Stores datetime as ISO string for persistence
    'is_game_active': False, # True only for game_queue[0] if started
}

# Set up Intents and Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

# --- Utility Functions ---

def format_time_remaining(seconds):
    """Converts seconds into a clean H/M string (e.g., '1h 5m')."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "a moment"

def generate_hint_ping_string():
    """Generates the ping string for all defined hint ping roles."""
    return "".join([f"<@&{role_id}> " for role_id in CONFIG['HINT_PING_ROLE_IDS']])

def generate_game_end_ping_string():
    """Generates the ping string for the single game end role."""
    role_id = CONFIG['GAME_END_PING_ROLE_ID']
    return f"<@&{role_id}>"

def find_next_available_slot():
    """Finds the next empty slot in the queue, or returns None if full."""
    # Check if we can append a new game
    if len(game_queue) < MAX_GAME_QUEUE_SIZE:
        # Return 1-based index
        return len(game_queue) + 1
    return None

def get_game_from_queue(position):
    """
    Retrieves a game state dictionary from the queue based on 1-based position.
    Returns (game_obj, internal_index) or (None, -1).
    """
    internal_index = position - 1
    if 0 <= internal_index < len(game_queue):
        return game_queue[internal_index], internal_index
    return None, -1

def get_active_game():
    """Returns the active game (Game 1) or None if the queue is empty or game is not started."""
    if game_queue and game_queue[0].get('is_game_active'):
        return game_queue[0]
    return None

def set_game_property(position, key, value):
    """Sets a property in a game object at a specific 1-based position."""
    game, idx = get_game_from_queue(position)
    if game:
        game[key] = value
        return True
    return False

async def start_next_game_in_queue(ctx=None):
    """
    Called upon a win or manual !start.
    Initializes and announces the new active game (Game 1).
    """
    global game_queue, last_guess_time
    
    if not game_queue:
        if ctx: await ctx.send("The game queue is empty. Please set up the next game using `!setitem`.")
        return False

    active_game = game_queue[0]

    # Ensure the game is fully configured before starting
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    if not active_game.get('item_name') or len(active_game.get('hints_storage', {})) != REQUIRED_HINTS:
        if ctx: await ctx.send(f"❌ Game 1 is incomplete (item/hints missing). Cannot start automatically. Please use `!setitem 1` and `!configureallhints 1`.")
        return False
    
    # 1. Clear cooldowns from the previous game
    last_guess_time = {}

    # 2. Set active state and timing
    active_game['is_game_active'] = True
    active_game['last_hint_reveal_time_iso'] = datetime.now().isoformat()
    
    # 3. Reveal the first hint
    first_hint_text = active_game['hints_storage'].get(1)
    active_game['hints_revealed'] = [{'hint_number': 1, 'text': first_hint_text}]
    
    # 4. Save state and start timer
    await save_game_state()
    if not hint_timer.is_running():
        hint_timer.start()

    # 5. Announce the start (using HINT_CHANNEL for the announcement)
    announcement_channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
    hint_timing_minutes = active_game['hint_timing_minutes']
    COOLDOWN_MINUTES = CONFIG['GUESS_COOLDOWN_MINUTES']
    ping_string = generate_hint_ping_string()

    if announcement_channel:
        start_message = (
            f'{ping_string}📢 **A new item guessing game has started!** (Game 1/{len(game_queue)} in queue)'
            f'Hints will be revealed every **{hint_timing_minutes} minutes**.'
            f'\n\n**First Hint (1/{REQUIRED_HINTS}):** _{first_hint_text}_'
            f'\n\nStart guessing with `!guess <item name>`! (Cooldown: {COOLDOWN_MINUTES} mins)'
        )
        await announcement_channel.send(start_message)
    
    await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))

    if ctx:
        await ctx.send(f"✅ Game 1 has started! The first hint has been sent to {announcement_channel.mention}.")

    print(f"New game started (Game 1). Item is {active_game['item_name']}")
    return True


# --- Custom Admin Check & Global Command Check (Unchanged) ---

def is_authorized_admin():
    """Custom check to ensure the user has one of the specific admin roles."""
    async def predicate(ctx):
        if not ctx.guild:
            return False
        member_roles = [role.id for role in ctx.author.roles]
        for required_id in CONFIG['ADMIN_ROLE_IDS']:
            if required_id in member_roles:
                return True
        return False
    return commands.check(predicate)

@bot.check
async def command_location_check(ctx):
    """Global check to restrict commands based on context."""
    if ctx.guild is None: return True

    if ctx.channel.category_id == CONFIG['TARGET_CATEGORY_ID']: return True

    if ctx.channel.id == CONFIG['WINS_CHANNEL_ID']:
        if ctx.command.name in ['wins', 'lbc', 'top', 'mywins', 'status']: return True
        else:
            await ctx.send("This channel is dedicated only to the leaderboard (`!wins`, `!mywins`).", delete_after=10)
            return False
    
    if ctx.author.guild_permissions.administrator and ctx.command.name in ['testping', 'status']: return True
    
    if ctx.channel.id != CONFIG['HINT_CHANNEL_ID'] and ctx.command.name in ['revealhint']: return True # Allow reveal hint anywhere for testing

    if not ctx.channel.category_id in [CONFIG['TARGET_CATEGORY_ID'], CONFIG['WINS_CHANNEL_ID']]:
        await ctx.send(f"❌ This command can only be used in the designated game category or wins channel.", delete_after=10)
        return False
    
    return True

# --- Data Persistence Functions (User Wins - Unchanged) ---

async def load_user_wins():
    """Loads user wins asynchronously using bot.loop.run_in_executor."""
    def sync_load():
        DATA_FILE = CONFIG['DATA_FILE']
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
                    return {int(k): v for k, v in data.items()}
            except json.JSONDecodeError:
                print("ERROR: user_wins.json is corrupted or empty.")
                return {}
        return {}

    global user_wins
    user_wins = await bot.loop.run_in_executor(executor, sync_load)
    print(f"Loaded {len(user_wins)} win records.")

async def save_user_wins():
    """Saves user wins asynchronously using bot.loop.run_in_executor."""
    def sync_save():
        DATA_FILE = CONFIG['DATA_FILE']
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(user_wins, f, indent=4)
                print("Win data saved.")
        except Exception as e:
            print(f"ERROR SAVING DATA: {e}")

    await bot.loop.run_in_executor(executor, sync_save)


# --- Game State Persistence Functions (Refactored for Queue) ---
async def save_game_state():
    """Saves the critical game state variables asynchronously, including the queue."""
    def sync_save():
        global game_queue, last_guess_time
        
        # Convert last_guess_time (int keys, datetime values)
        serialized_last_guess_time = {
            str(k): v.isoformat() for k, v in last_guess_time.items()
        }

        # The queue already uses ISO strings for hint reveal time via 'last_hint_reveal_time_iso'
        # We ensure all game objects are dictionaries.
        state = {
            'game_queue': game_queue,
            'last_guess_time': serialized_last_guess_time
        }
        
        try:
            with open(CONFIG['GAME_STATE_FILE'], 'w') as f:
                json.dump(state, f, indent=4)
                print("Game state queue saved.")
        except Exception as e:
            print(f"ERROR SAVING GAME STATE: {e}")

    await bot.loop.run_in_executor(executor, sync_save)

async def load_game_state():
    """Loads the game state queue from a JSON file asynchronously."""
    def sync_load():
        STATE_FILE = CONFIG['GAME_STATE_FILE']
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print("ERROR: game_state_queue.json is corrupted or empty.")
                return {}
        return {}

    global game_queue, last_guess_time
    
    state = await bot.loop.run_in_executor(executor, sync_load)
    
    if state:
        game_queue = state.get('game_queue', [])
            
        # Load guess cooldown state
        loaded_guess_times = state.get('last_guess_time', {})
        # Convert keys (string user IDs) back to int and values (ISO strings) back to datetime
        last_guess_time = {
            int(k): datetime.fromisoformat(v) for k, v in loaded_guess_times.items()
        }

        print(f"Game state queue loaded. Games in queue: {len(game_queue)}. Cooldown entries loaded: {len(last_guess_time)}")
    else:
        game_queue = []
        print("No game state queue loaded.")

# --- END Game State Persistence Functions ---


# --- Timed Hint Task (Refactored for Queue) ---
@tasks.loop(minutes=1)
async def hint_timer():
    global game_queue
    
    active_game = get_active_game()
    
    # Check if bot is ready and game is active
    if not bot.is_ready() or not active_game:
        return
        
    now = datetime.now()
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    
    # Extract game-specific state from the active game object
    last_hint_reveal_time_iso = active_game.get('last_hint_reveal_time_iso')
    if not last_hint_reveal_time_iso:
        print("Warning: Active game has no last_hint_reveal_time_iso set.")
        return
        
    last_hint_reveal_time = datetime.fromisoformat(last_hint_reveal_time_iso)
    hint_timing_minutes = active_game['hint_timing_minutes']
    current_hints_revealed = active_game['hints_revealed']
    current_hints_storage = active_game['hints_storage']

    next_reveal_time = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    
    try:
        # Check if the next hint is due AND if we haven't revealed all configured hints
        next_hint_number = len(current_hints_revealed) + 1
        
        if next_hint_number > REQUIRED_HINTS:
            # All hints revealed, stop the timer for this game
            print(f"Hint timer stopping: All {REQUIRED_HINTS} hints revealed for active game.")
            return # Stop processing, but do not stop the loop, just return.

        if now >= next_reveal_time:
            
            if next_hint_number in current_hints_storage:
                channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
                
                if channel:
                    hint_text = current_hints_storage[next_hint_number]
                    ping_string = generate_hint_ping_string()
                    
                    ping_message = (
                        f"{ping_string}📢 **New Hint ({next_hint_number}/{REQUIRED_HINTS}):** "
                        f"_{hint_text}_"
                    )

                    await channel.send(ping_message)
                    
                    # Update state in the active game object
                    active_game['hints_revealed'].append({'hint_number': next_hint_number, 'text': hint_text})
                    active_game['last_hint_reveal_time_iso'] = now.isoformat()
                    await save_game_state()
                else:
                    print(f"Warning: Hint channel ID {CONFIG['HINT_CHANNEL_ID']} not found.")
                    
    except Exception as e:
        print(f"ERROR in hint_timer task: {e}", file=sys.stderr)


# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    await load_user_wins()
    await load_game_state() # Load game state queue on startup
    
    active_game = get_active_game()

    if active_game:
        await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))
        print(f"Resuming active game for item: {active_game['item_name']}")
    elif game_queue:
        await bot.change_presence(activity=discord.Game(name=f"Ready to start Game 1 (!start)"))
    else:
        await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
        
    # CRITICAL FIX: Ensure timer starts on ready if a game is active
    if active_game and not hint_timer.is_running():
        hint_timer.start()
        print("Hint timer started/restarted on bot startup.")

# --- Utility Functions (Role Management) ---
# (award_winner_roles is unchanged)
async def award_winner_roles(member: discord.Member):
    global user_wins

    user_id = member.id
    guild = member.guild
    WINNER_ROLES_CONFIG = CONFIG['WINNER_ROLES_CONFIG']
    
    user_wins[user_id] = user_wins.get(user_id, 0) + 1
    wins_count = user_wins[user_id]
    await save_user_wins()
    
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
            print(f"Permission Error: Cannot add/remove role for {member.display_name}.", file=sys.stderr)
        except Exception as e:
            print(f"Error managing role: {e}", file=sys.stderr)

# --- Admin Commands (Refactored for Queue) ---

@bot.command(name='setitem', help='[ADMIN] Sets the item name for a game (1-5, defaults to next available).')
@is_authorized_admin()
async def set_item_name(ctx, item_name: str, position: int = None):
    global game_queue
    
    if position is None:
        position = find_next_available_slot()
        if position is None:
            return await ctx.send(f"❌ Error: Game queue is full (max {MAX_GAME_QUEUE_SIZE} games). Use `!delete` to free a slot.")
    
    if not 1 <= position <= MAX_GAME_QUEUE_SIZE:
        return await ctx.send(f"❌ Position must be between 1 and {MAX_GAME_QUEUE_SIZE}.")

    if position == 1 and get_active_game():
        return await ctx.send("❌ Cannot change the item for the **active** Game 1. Use `!endgame` first, or set Game 2-5.")

    # Ensure the game object exists in the queue up to this position
    while len(game_queue) < position:
        # Add a new, clean game template to the queue
        game_queue.append(GAME_TEMPLATE.copy())

    # Update the item name
    game_obj = game_queue[position - 1]
    game_obj['item_name'] = item_name.strip()
    game_obj['is_game_active'] = False # Ensure future games are not marked active

    await save_game_state()
    
    # Check configuration status
    configured_hints = len(game_obj.get('hints_storage', {}))
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    
    status_msg = ""
    if configured_hints == REQUIRED_HINTS:
        status_msg = "Game is fully configured!"
    else:
        status_msg = f"Waiting for hints: {configured_hints}/{REQUIRED_HINTS} configured."

    await ctx.send(f"✅ Item set for **Game {position}** to: **{item_name}**. {status_msg}")
    if position == 1 and len(game_queue[0].get('hints_storage', {})) == REQUIRED_HINTS and not get_active_game():
        await bot.change_presence(activity=discord.Game(name=f"Ready! (!start)"))


@bot.command(name='configureallhints', help=f'[ADMIN] Sets all {CONFIG["REQUIRED_HINTS"]} hints for a game (1-5, defaults to next available).')
@is_authorized_admin()
async def configure_all_hints(ctx, *, hints_text: str, position: int = None):
    global game_queue
    
    # Split position from hints_text if provided
    parts = hints_text.split()
    if parts and parts[-1].isdigit():
        try:
            p = int(parts[-1])
            if 1 <= p <= MAX_GAME_QUEUE_SIZE:
                position = p
                hints_text = hints_text.rsplit(' ', 1)[0] # Remove position from text
        except ValueError:
            pass # Not a valid position at the end, continue with the entire text

    if position is None:
        position = find_next_available_slot()
        if position is None:
            return await ctx.send(f"❌ Error: Game queue is full (max {MAX_GAME_QUEUE_SIZE} games). Use `!delete` to free a slot.")
    
    if not 1 <= position <= MAX_GAME_QUEUE_SIZE:
        return await ctx.send(f"❌ Position must be between 1 and {MAX_GAME_QUEUE_SIZE}.")

    if position == 1 and get_active_game():
        return await ctx.send("❌ Cannot change hints for the **active** Game 1. Use `!endgame` first, or set Game 2-5.")

    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    
    # Split the input text by newline characters, handling potential leading/trailing whitespace
    hint_lines = [line.strip() for line in hints_text.split('\n') if line.strip()]

    if len(hint_lines) != REQUIRED_HINTS:
        return await ctx.send(
            f"❌ Error: You must provide exactly **{REQUIRED_HINTS}** hints, one per line. "
            f"You provided {len(hint_lines)}. Please ensure you use a multi-line code block in Discord."
        )
    
    # Ensure the game object exists in the queue up to this position
    while len(game_queue) < position:
        game_queue.append(GAME_TEMPLATE.copy())

    # Clear existing hints and set the new ones
    game_obj = game_queue[position - 1]
    game_obj['hints_storage'] = {i: hint_text for i, hint_text in enumerate(hint_lines, 1)}
    game_obj['hints_revealed'] = []
    game_obj['is_game_active'] = False # Ensure future games are not marked active

    await save_game_state()

    status_msg = ""
    if game_obj.get('item_name'):
        status_msg = f"Item is set to **{game_obj['item_name']}**."
    else:
        status_msg = "Item is NOT yet set (`!setitem`)."

    await ctx.send(
        f"✅ Successfully set **all {REQUIRED_HINTS} hints** for **Game {position}** at once! {status_msg}"
    )
    if position == 1 and game_obj.get('item_name') and not get_active_game():
        await bot.change_presence(activity=discord.Game(name=f"Ready! (!start)"))


@bot.command(name='sethinttiming', help='[ADMIN] Sets the hint interval (in minutes) for a game (1-5, defaults to next available).')
@is_authorized_admin()
async def set_hint_timing(ctx, minutes: int, position: int = None):
    global game_queue
    
    if position is None:
        position = 1 # Default to active game timing for change

    if not 1 <= position <= MAX_GAME_QUEUE_SIZE:
        return await ctx.send(f"❌ Position must be between 1 and {MAX_GAME_QUEUE_SIZE}.")

    if minutes < 1 or minutes > 60:
        return await ctx.send("Interval must be between 1 and 60 minutes.")

    game_obj, idx = get_game_from_queue(position)
    if not game_obj:
        return await ctx.send(f"❌ Game {position} is not set up in the queue.")
    
    game_obj['hint_timing_minutes'] = minutes
    await save_game_state()
    
    await ctx.send(f"✅ Hint revealing interval for **Game {position}** set to **{minutes} minutes**.")


@bot.command(name='revealhint', help='[ADMIN] Immediately reveals the next sequential hint for the active game (Game 1).')
@is_authorized_admin()
async def reveal_hint_manual(ctx):
    global game_queue

    active_game = get_active_game()

    if not active_game:
        return await ctx.send("❌ Cannot reveal a hint: No game is currently active.")
    
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    current_hints_revealed = active_game['hints_revealed']
    current_hints_storage = active_game['hints_storage']

    next_hint_number = len(current_hints_revealed) + 1

    if next_hint_number > REQUIRED_HINTS:
        return await ctx.send(f"❌ All **{REQUIRED_HINTS}** hints have already been revealed for Game 1.")

    if next_hint_number in current_hints_storage:
        channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
        
        if channel:
            hint_text = current_hints_storage[next_hint_number]
            ping_string = generate_hint_ping_string()
            
            ping_message = (
                f"{ping_string}📢 **Manual Hint Reveal (Game 1 - {next_hint_number}/{REQUIRED_HINTS}):** "
                f"_{hint_text}_"
            )

            await channel.send(ping_message)
            
            # Update game state in the active object
            active_game['hints_revealed'].append({'hint_number': next_hint_number, 'text': hint_text})
            active_game['last_hint_reveal_time_iso'] = datetime.now().isoformat()
            await save_game_state()
            
            await ctx.send(f"✅ Hint **{next_hint_number}** has been manually revealed in {channel.mention}. The timer has been reset.")
        else:
            await ctx.send(f"❌ Error: Hint channel ID {CONFIG['HINT_CHANNEL_ID']} not found. Please check configuration.")
    else:
        await ctx.send(f"❌ Hint **{next_hint_number}** is not configured in Game 1. Please ensure you have set all {REQUIRED_HINTS} hints.")


@bot.command(name='endgame', help='[ADMIN] Forcefully ends the active game (Game 1) and shifts the queue.')
@is_authorized_admin()
async def end_game(ctx):
    global game_queue
    
    if not game_queue:
        return await ctx.send("❌ The game queue is already empty.")

    # Stop the timer if it's running
    if hint_timer.is_running():
        hint_timer.stop()
    
    # Remove the active game and shift the queue
    ended_game = game_queue.pop(0)
    await ctx.send(f"🚨 **Active Game (Game 1) Forcefully Ended.** Item was: **{ended_game.get('item_name', 'N/A')}**.")
    
    # Attempt to start the next game
    if game_queue:
        await ctx.send("🚀 **Queue shifted.** Attempting to auto-start the new Game 1...")
        success = await start_next_game_in_queue(ctx)
        if not success:
            await bot.change_presence(activity=discord.Game(name=f"Game 1 setup needed (!setitem 1)"))
    else:
        await save_game_state()
        await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
        await ctx.send("Queue is now empty. Please set up a new game using `!setitem`.")


@bot.command(name='delete', help='[ADMIN] Deletes a specific game by position (1-5).')
@is_authorized_admin()
async def delete_game(ctx, position: int):
    global game_queue
    
    if not 1 <= position <= MAX_GAME_QUEUE_SIZE:
        return await ctx.send(f"❌ Position must be between 1 and {MAX_GAME_QUEUE_SIZE}.")

    game_to_delete, idx = get_game_from_queue(position)
    
    if not game_to_delete:
        return await ctx.send(f"❌ Game {position} does not exist in the current queue.")
        
    if position == 1 and game_to_delete.get('is_game_active'):
        return await ctx.send("❌ Cannot delete the **active** Game 1. Use `!endgame` first.")
    
    # Delete the game and shift the queue
    del game_queue[idx]
    await save_game_state()
    
    await ctx.send(f"✅ **Game {position}** (Item: **{game_to_delete.get('item_name', 'N/A')}**) has been deleted. The queue has been shifted.")
    
    # Update presence if game 1 was deleted and no game is active
    if position == 1 and not get_active_game():
        if game_queue:
            await bot.change_presence(activity=discord.Game(name=f"Ready to start Game 1 (!start)"))
        else:
            await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))


@bot.command(name='deletequeue', help='[ADMIN] Clears all games from the queue. Requires confirmation.')
@is_authorized_admin()
async def delete_queue_confirmation(ctx):
    if not game_queue:
        return await ctx.send("The game queue is already empty.")

    # Use a custom response instead of alert()
    embed = discord.Embed(
        title="⚠️ Confirmation Required",
        description=f"Are you sure you want to delete all **{len(game_queue)}** games from the queue? This action cannot be undone.",
        color=discord.Color.red()
    )
    
    # Create a temporary message and wait for an admin response
    confirm_msg = await ctx.send(
        content="Please type `CONFIRM DELETE ALL` in this channel to proceed.",
        embed=embed
    )
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.upper() == 'CONFIRM DELETE ALL'

    try:
        # Wait for the admin to confirm (15 seconds timeout)
        await bot.wait_for('message', check=check, timeout=15.0)
        
        global game_queue, last_guess_time
        
        if hint_timer.is_running():
            hint_timer.stop()

        game_queue = []
        last_guess_time = {} # Clear cooldowns
        await save_game_state()
        
        await ctx.send("🗑️ **SUCCESS:** The entire game queue has been cleared.")
        await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
        
    except concurrent.futures.TimeoutError:
        await ctx.send("🚫 Queue deletion cancelled due to timeout.")
    finally:
        # Clean up the confirmation message
        await confirm_msg.delete()


@bot.command(name='status', help='Displays the active game status or configuration for a specific game (1-5).')
@is_authorized_admin() # Keep this admin only as it shows the answer implicitly for future games
async def game_status(ctx, position: int = None):
    global game_queue, last_guess_time

    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    
    if not game_queue:
        return await ctx.send(f"The game queue is empty. **0/{MAX_GAME_QUEUE_SIZE}** games configured.")

    if position is None:
        # Show the general queue overview
        active_game = get_active_game()
        
        status_emoji = "🟢 ACTIVE" if active_game else "🔴 INACTIVE"
        queue_status_title = f"🎮 Game Queue Status ({len(game_queue)}/{MAX_GAME_QUEUE_SIZE} Configured)"

        embed = discord.Embed(
            title=queue_status_title,
            description=f"Active Status: **{status_emoji}**\nItem: **{active_game['item_name'] if active_game else 'N/A'}**",
            color=discord.Color.blue()
        )
        
        # List all games in the queue
        for i, game in enumerate(game_queue, 1):
            item_name = game.get('item_name', 'Not Set')
            is_active = game.get('is_game_active', False)
            hints_configured = len(game.get('hints_storage', {}))
            
            status_icon = "▶️" if is_active else "✅" if item_name != 'Not Set' and hints_configured == REQUIRED_HINTS else "🛠️"
            
            field_value = (
                f"Item: **{item_name}**\n"
                f"Hints: {hints_configured}/{REQUIRED_HINTS} configured\n"
                f"Timing: {game.get('hint_timing_minutes', CONFIG['DEFAULT_HINT_TIMING_MINUTES'])} min"
            )
            embed.add_field(name=f"{status_icon} Game {i}", value=field_value, inline=True)
            
        embed.set_footer(text=f"Total active cooldowns: {len(last_guess_time)}. Use !status <pos> for detailed game info.")
        await ctx.send(embed=embed)
        return

    # Show detailed status for a specific game
    if not 1 <= position <= MAX_GAME_QUEUE_SIZE:
        return await ctx.send(f"❌ Position must be between 1 and {MAX_GAME_QUEUE_SIZE}.")

    game_obj, idx = get_game_from_queue(position)
    if not game_obj:
        return await ctx.send(f"❌ Game {position} is not set up in the queue.")

    # Game Status Check
    is_active = game_obj.get('is_game_active', False)
    status_emoji = "🟢 ACTIVE (Game 1)" if is_active else "🔴 INACTIVE"
    
    # Answer Status Check
    item_name = game_obj.get('item_name')
    answer_status = f"**{item_name}**" if item_name else "❌ Not Set"

    # Hint Configuration Status
    configured_hints = len(game_obj.get('hints_storage', {}))
    hint_status = f"✅ All {REQUIRED_HINTS} hints configured." if configured_hints == REQUIRED_HINTS else f"⚠️ {configured_hints}/{REQUIRED_HINTS} hints configured."

    # Revealed Hints Status
    revealed_count = len(game_obj.get('hints_revealed', []))
    revealed_text = f"{revealed_count} / {configured_hints} Revealed."
    
    # Next Hint Time (Only relevant if active)
    next_hint_time_str = "N/A"
    if is_active and game_obj.get('last_hint_reveal_time_iso'):
        last_reveal = datetime.fromisoformat(game_obj['last_hint_reveal_time_iso'])
        next_reveal = last_reveal + timedelta(minutes=game_obj['hint_timing_minutes'])
        time_until_next = next_reveal - datetime.now()
        
        if time_until_next.total_seconds() > 0:
            seconds = int(time_until_next.total_seconds())
            next_hint_time_str = f"In {format_time_remaining(seconds)}"
        else:
            next_hint_time_str = "⏳ Due now"

    # Construct the Embed
    embed = discord.Embed(
        title=f"🔎 Game {position} Detailed Status",
        description=f"Status: **{status_emoji}**",
        color=discord.Color.orange() if is_active else discord.Color.greyple()
    )
    
    embed.add_field(name="Correct Answer", value=answer_status, inline=False)
    
    hint_details = (
        f"**Required:** {REQUIRED_HINTS}\n"
        f"**Configured:** {hint_status}\n"
        f"**Interval:** {game_obj['hint_timing_minutes']} minutes"
    )
    embed.add_field(name="Hint Configuration", value=hint_details, inline=True)
    
    timer_details = (
        f"**Revealed:** {revealed_count} of {REQUIRED_HINTS}\n"
        f"**Next Reveal:** {next_hint_time_str}"
    )
    embed.add_field(name="Hint Timer (If Active)", value=timer_details, inline=True)
    
    await ctx.send(embed=embed)


@bot.command(name='start', help='[ADMIN] Starts the first game in the queue (Game 1).')
@is_authorized_admin()
async def start_game(ctx):
    global game_queue
    
    if get_active_game():
        return await ctx.send("A game is already running! Use `!guess` or `!endgame` to stop the current one.")

    if not game_queue:
        return await ctx.send("❌ The queue is empty. Please set up Game 1 using `!setitem`.")

    success = await start_next_game_in_queue(ctx)
    if not success:
        await bot.change_presence(activity=discord.Game(name=f"Game 1 setup needed (!setitem 1)"))


# --- Game Commands (Refactored for Queue) ---

@bot.command(name='guess', help='Attempts to guess the item name for the active game.')
async def guess_item(ctx, *, guess: str):
    global game_queue, last_guess_time # FIX: Ensure this is the absolute first statement.

    active_game = get_active_game()
    
    if not active_game:
        return await ctx.send("No active game. Start a new one with `!start`.")
    
    # Check cooldown
    user_id = ctx.author.id
    now = datetime.now()
    cooldown_minutes = CONFIG['GUESS_COOLDOWN_MINUTES']
    
    if user_id in last_guess_time:
        time_since_last_guess = now - last_guess_time[user_id]
        if time_since_last_guess < timedelta(minutes=cooldown_minutes):
            remaining_time = timedelta(minutes=cooldown_minutes) - time_since_last_guess
            seconds = int(remaining_time.total_seconds())
            time_remaining_str = format_time_remaining(seconds)
            
            await ctx.reply(f"🛑 **Cooldown Active:** You must wait **{time_remaining_str}** before guessing again.", delete_after=5)
            return

    # Record the new guess time
    last_guess_time[user_id] = now
    await save_game_state()
    
    corre
