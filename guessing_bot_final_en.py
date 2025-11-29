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
    return "Item Guessing Bot Worker is Running! (Keep-Alive Active)", 200

def run_flask():
    """Starts the Flask server in a separate thread to prevent blocking the Discord bot."""
    # Use a safe environment variable for deployment (Render uses PORT)
    try:
        # debug=False is critical when running in a multi-threaded deployment environment
        app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
    except Exception as e:
        print(f"Error starting Flask server: {e}")

# --- BOT CONFIGURATION AND CONSTANTS ---
# TOKEN is read via os.getenv('DISCORD_TOKEN') below

# Centralized Configuration Dictionary - IDs updated with user-provided labels
CONFIG = {
    # File Persistence
    'DATA_FILE': 'user_wins.json',
    'GAME_STATE_FILE': 'game_state.json', # File for game state persistence
    
    # Game Parameters
    'REQUIRED_HINTS': 7,
    'GUESS_COOLDOWN_MINUTES': 30,
    # Default hint time is 60 minutes (1 hour)
    'DEFAULT_HINT_TIMING_MINUTES': 60, # Initial value, modified by !sethinttiming

    # Channel and Category IDs (***UPDATE THESE PLACEHOLDERS***)
    'TARGET_CATEGORY_ID': 1441691009993146490, # Main game category ID
    'WINS_CHANNEL_ID': 1442057049805422693,      # Channel for !wins command only
    'WINNER_ANNOUNCEMENT_CHANNEL_ID': 1441858034291708059, # Channel for announcing the winner
    'HINT_CHANNEL_ID': 1441386236844572834,      # Channel for periodic hint announcements
    
    # Role IDs
    'ADMIN_ROLE_IDS': [
        1397641683205624009, # Admin: Support Team
        1441386642332979200 # Admin: Host
    ],
    # Role to ping on every new hint reveal
    'HINT_PING_ROLE_IDS': [
        1441388270201077882 # Ping Role
    ],
    # Role to ping when the game ends (signals an admin to set up a new game)
    'GAME_END_PING_ROLE_ID': 1441386642332979200, # Host role

    # Winner Roles (Key: minimum wins required, Value: Role ID)
    'WINNER_ROLES_CONFIG': {
        1:   1441693698776764486,
        5:   1441693984266129469,
        10:      1441694043477381150,
        25:      1441694109268967505,
        50:      1441694179011989534,
        100:     1441694438345674855
    }
}

# --- Game State Variables ---
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
is_game_active = False
# Initialize using the updated CONFIG value (60 minutes)
hint_timing_minutes = CONFIG['DEFAULT_HINT_TIMING_MINUTES'] 
last_hint_reveal_time = None
user_wins = {}
# Dictionary to track last guess time for cooldown
last_guess_time = {} 

# Set up Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Required for reliable role management and leaderboard
bot = commands.Bot(command_prefix='!', intents=intents)

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
    pings = "".join([f"<@&{role_id}> " for role_id in CONFIG['HINT_PING_ROLE_IDS']])
    # Diagnostic print to confirm the generated ping string
    print(f"DIAG: Generated hint ping string: '{pings.strip()}'") 
    return pings

def generate_game_end_ping_string():
    """Generates the ping string for the single game end role."""
    role_id = CONFIG['GAME_END_PING_ROLE_ID']
    ping = f"<@&{role_id}>"
    print(f"DIAG: Generated game end ping string: '{ping}'")
    return ping

# --- Custom Admin Check ---

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

# --- Global Command Location Check ---

@bot.check
async def command_location_check(ctx):
    """Global check to restrict commands based on context."""
    if ctx.guild is None:
        return True # Allow DMs

    # Check 1: Command is in the main game category (Most commands work here)
    if ctx.channel.category_id == CONFIG['TARGET_CATEGORY_ID']:
        return True

    # Check 2: Command is in the specific leaderboard channel (!wins allowed, others blocked)
    if ctx.channel.id == CONFIG['WINS_CHANNEL_ID']:
        if ctx.command.name in ['wins', 'lbc', 'top', 'mywins']: # Added 'mywins' to the allowed list
            return True # !wins and !mywins are allowed
        else:
            # Block all other commands (!guess, !start, etc.)
            await ctx.send("This channel is dedicated only to the leaderboard (`!wins`, `!mywins`). Guessing and game control must take place in the main game category.", delete_after=10)
            return False
    
    # Check 3: Command is in any other channel or category
    if ctx.command.name == 'testping' and ctx.author.guild_permissions.administrator:
        # Allow testping for administrators anywhere for diagnostic purposes
        return True
    
    await ctx.send(f"❌ This command can only be used in the designated game category or wins channel.", delete_after=10)
    return False

# --- Data Persistence Functions (User Wins) ---
def load_user_wins():
    global user_wins
    DATA_FILE = CONFIG['DATA_FILE']
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                # Ensure keys are integers (Discord IDs)
                user_wins = {int(k): v for k, v in data.items()}
                print(f"Loaded {len(user_wins)} win records.")
        except json.JSONDecodeError:
            print("ERROR: user_wins.json is corrupted or empty. Starting with empty data.")
            user_wins = {}
    else:
        user_wins = {}

def save_user_wins():
    DATA_FILE = CONFIG['DATA_FILE']
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(user_wins, f, indent=4)
            print("Win data saved.")
    except Exception as e:
        print(f"ERROR SAVING DATA: {e}")

# --- Game State Persistence Functions ---
def save_game_state():
    """Saves the critical game state variables to a JSON file."""
    global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time, hint_timing_minutes
    
    # Prepare the state for JSON serialization
    state = {
        'is_game_active': is_game_active,
        'correct_answer': correct_answer,
        # Convert keys of current_hints_storage (int) to strings for JSON
        'current_hints_storage': {str(k): v for k, v in current_hints_storage.items()},
        'current_hints_revealed': current_hints_revealed,
        # Convert datetime object to ISO 8601 string for persistence
        'last_hint_reveal_time': last_hint_reveal_time.isoformat() if last_hint_reveal_time else None,
        'hint_timing_minutes': hint_timing_minutes
    }
    
    try:
        with open(CONFIG['GAME_STATE_FILE'], 'w') as f:
            json.dump(state, f, indent=4)
            print("Game state saved.")
    except Exception as e:
        print(f"ERROR SAVING GAME STATE: {e}")

def load_game_state():
    """Loads the game state from a JSON file."""
    global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time, hint_timing_minutes
    
    STATE_FILE = CONFIG['GAME_STATE_FILE']
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                
                is_game_active = state.get('is_game_active', False)
                correct_answer = state.get('correct_answer')
                # Convert keys of current_hints_storage (string) back to integers
                current_hints_storage = {int(k): v for k, v in state.get('current_hints_storage', {}).items()}
                current_hints_revealed = state.get('current_hints_revealed', [])
                hint_timing_minutes = state.get('hint_timing_minutes', CONFIG['DEFAULT_HINT_TIMING_MINUTES'])
                
                last_time_str = state.get('last_hint_reveal_time')
                if last_time_str:
                    # Parse the ISO 8601 string back into a datetime object
                    last_hint_reveal_time = datetime.fromisoformat(last_time_str)
                else:
                    last_hint_reveal_time = None

                print(f"Game state loaded. Active: {is_game_active}")
                
        except json.JSONDecodeError:
            print("ERROR: game_state.json is corrupted or empty. Starting fresh.")
            is_game_active = False
    
# --- END Game State Persistence Functions ---


# --- Timed Hint Task ---
@tasks.loop(minutes=1)
async def hint_timer():
    global current_hints_revealed, last_hint_reveal_time, current_hints_storage, hint_timing_minutes
    
    # Check if the bot is ready and game is active
    if not bot.is_ready() or not is_game_active or not last_hint_reveal_time or not current_hints_storage:
        return
        
    now = datetime.now()
    next_reveal_time = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    
    try:
        if now >= next_reveal_time:
            next_hint_number = len(current_hints_revealed) + 1
            REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
            
            if next_hint_number in current_hints_storage:
                # USE THE DEDICATED CHANNEL FOR AUTOMATIC HINTS
                channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
                
                if channel:
                    hint_text = current_hints_storage[next_hint_number]
                    
                    # Use the new utility function for ping string
                    ping_string = generate_hint_ping_string()
                    
                    # Construct the message including the role pings
                    ping_message = (
                        f"{ping_string}📢 **New Hint ({next_hint_number}/{REQUIRED_HINTS}):** "
                        f"_{hint_text}_"
                    )

                    await channel.send(ping_message)
                    
                    # Store the revealed hint and reset the timer
                    current_hints_revealed.append({'hint_number': next_hint_number, 'text': hint_text}) 
                    last_hint_reveal_time = now
                    save_game_state() # SAVE STATE after a hint reveal
                else:
                    print(f"Warning: Hint channel ID {CONFIG['HINT_CHANNEL_ID']} not found.")
            
            else:
                # All hints revealed, stop the timer
                if hint_timer.is_running():
                    hint_timer.stop()
                    print("Hint timer stopped: All hints revealed.")
                    
    except Exception as e:
        # Log the error but allow the loop to continue next minute
        print(f"ERROR in hint_timer task: {e}")

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    load_user_wins()
    load_game_state() # Load game state on startup
    
    if is_game_active:
        await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))
        print(f"Resuming active game for item: {correct_answer}")
    else:
        await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
        
    # CRITICAL FIX: Ensure timer starts on ready based on the loaded state
    if not hint_timer.is_running():
        hint_timer.start()
        print("Hint timer started/restarted on bot startup.")


# --- Utility Functions ---
async def award_winner_roles(member: discord.Member):
    global user_wins

    user_id = member.id
    guild = member.guild
    WINNER_ROLES_CONFIG = CONFIG['WINNER_ROLES_CONFIG']
    
    # 1. Update and save win count
    # NOTE: user_wins has already been updated in the !guess command, but we ensure it's loaded here
    # The count will be incremented *again* by one if this function is called *before* the count is updated in !guess.
    # To fix this, we'll assume the caller (the !guess function) handles the increment.
    # The original implementation of !guess was missing, so we must assume the increment happens *before* this call
    # in the caller function, or we handle it here. Since the caller was incomplete, we'll keep the increment logic here
    # for self-contained functionality, but we must be aware of potential double-increment if called incorrectly.
    # *** Corrected Logic: Let's assume !guess handles the increment, and this function only manages roles based on the new count.
    
    # Check if the win has been recorded by the caller (!guess)
    wins_count = user_wins.get(user_id, 0)
    
    # If the bot is called immediately after a win, the count will be 0 if the user had no previous wins. 
    # Let's trust the !guess implementation (which will be completed below) to manage user_wins before calling this.
    # We will ensure the final !guess implementation does the increment and save *before* calling this.
    
    # Rereading the original code: The original code in !guess was incomplete, so let's put the increment logic here
    # as a safety measure, while noting the ideal place for the increment is *before* this function call.
    # I will modify the !guess completion to call this *after* the increment.
    
    # REVISED: Since I am completing !guess, I will put the increment/save logic inside !guess, 
    # and this function will only read the total wins.
    
    wins_count = user_wins.get(user_id, 0) # Get the newly updated count

    # 2. Find the highest tier role the user qualifies for
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
        
        # Identify lower-tier roles to remove (only those from the WINNER_ROLES_CONFIG)
        roles_to_remove = [
            role for role in member.roles 
            if role.id in all_winner_role_ids and role.id != achieved_role_id
        ]

        try:
            # Add the new or current highest role
            role_added = False
            if target_role not in member.roles:
                await member.add_roles(target_role)
                role_added = True
            
            # Remove redundant lower-tier roles
            if roles_to_remove:
                # Create a list of names for the DM notification
                removed_names = [role.name for role in roles_to_remove]
                await member.remove_roles(*roles_to_remove)
                
            if role_added:
                # Only send DM if a new tier was achieved
                await member.send(f"You've reached {wins_count} wins and earned the role **{target_role.name}**! " + 
                                  (f"Your previous tier role(s) ({', '.join(removed_names)}) have been removed." if removed_names else ""))
                
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
    save_game_state() # Save state after setting item
    await ctx.send(f"✅ Correct item set to: **{correct_answer}**.")
    await bot.change_presence(activity=discord.Game(name=f"Waiting for hints (!sethint or !setallhints)"))


@bot.command(name='sethint', help=f"[ADMIN] Sets hints 1 through {CONFIG['REQUIRED_HINTS']}. Usage: !sethint 1 This is the first hint...")
@is_authorized_admin()
async def set_hint(ctx, number: int, *, hint_text: str):
    global is_game_active, current_hints_storage

    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']

    if is_game_active:
        await ctx.send("Cannot modify hints while a game is running.")
        return
    
    if not 1 <= number <= REQUIRED_HINTS: 
        await ctx.send(f"❌ Hint number must be between 1 and {REQUIRED_HINTS}.")
        return

    current_hints_storage[number] = hint_text.strip()
    
    current_count = len(current_hints_storage)
    
    # Announce the current number of configured hints
    if current_count == REQUIRED_HINTS:
        save_game_state() # Save state when fully configured
        await ctx.send(f"✅ Hint No. **{number}/{REQUIRED_HINTS}** has been set. **All {REQUIRED_HINTS} hints are now configured!**")
        if correct_answer:
            await bot.change_presence(activity=discord.Game(name=f"Ready! (!start)"))
    else:
        await ctx.send(f"✅ Hint No. **{number}/{REQUIRED_HINTS}** has been set. Currently configured hints: **{current_count}/{REQUIRED_HINTS}**.")


@bot.command(name='setallhints', help=f'[ADMIN] Sets all {CONFIG["REQUIRED_HINTS"]} hints at once, separated by new lines.')
@is_authorized_admin()
async def set_all_hints(ctx, *, hints_text: str):
    global is_game_active, current_hints_storage

    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']

    if is_game_active:
        await ctx.send("Cannot modify hints while a game is running.")
        return
    
    # Split the input text by newline characters, handling potential leading/trailing whitespace
    # We filter out empty lines that might result from trailing newlines or extra spacing.
    hint_lines = [line.strip() for line in hints_text.split('\n') if line.strip()]

    if len(hint_lines) != REQUIRED_HINTS: 
        await ctx.send(
            f"❌ Error: You must provide exactly **{REQUIRED_HINTS}** hints, one per line. "
            f"You provided {len(hint_lines)}. Please ensure you use a multi-line code block in Discord."
        )
        return
    
    # Clear existing hints and set the new ones
    current_hints_storage = {}
    for i, hint_text in enumerate(hint_lines, 1):
        current_hints_storage[i] = hint_text

    save_game_state() # Save state when fully configured

    await ctx.send(
        f"✅ Successfully set **all {REQUIRED_HINTS} hints** at once! The game is ready to start."
    )
    if correct_answer:
        await bot.change_presence(activity=discord.Game(name=f"Ready! (!start)"))


@bot.command(name='sethinttiming', help='[ADMIN] Sets the interval for revealing hints (in minutes).')
@is_authorized_admin()
async def set_hint_timing(ctx, minutes: int):
    global hint_timing_minutes

    if is_game_active:
        await ctx.send("Cannot change timing while a game is running.")
        return

    # Maximum 60 minutes allowed for manual change
    if minutes < 1 or minutes > 60:
        await ctx.send("Interval must be between 1 and 60 minutes.")
        return
    
    hint_timing_minutes = minutes
    save_game_state() # Save state after setting timing
    await ctx.send(f"✅ Hint revealing interval set to **{minutes} minutes**.")


@bot.command(name='revealhint', help='[ADMIN] Immediately reveals the next sequential hint.')
@is_authorized_admin()
async def reveal_hint_manual(ctx):
    global current_hints_revealed, last_hint_reveal_time, current_hints_storage

    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']

    if not is_game_active:
        return await ctx.send("❌ Cannot reveal a hint: No game is currently active.")
    
    next_hint_number = len(current_hints_revealed) + 1

    if next_hint_number > REQUIRED_HINTS:
        return await ctx.send(f"❌ All **{REQUIRED_HINTS}** hints have already been revealed.")

    if next_hint_number in current_hints_storage:
        channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
        
        if channel:
            hint_text = current_hints_storage[next_hint_number]
            ping_string = generate_hint_ping_string()
            
            ping_message = (
                f"{ping_string}📢 **Manual Hint Reveal ({next_hint_number}/{REQUIRED_HINTS}):** "
                f"_{hint_text}_"
            )

            await channel.send(ping_message)
            
            # Update game state
            current_hints_revealed.append({'hint_number': next_hint_number, 'text': hint_text}) 
            last_hint_reveal_time = datetime.now() # Reset the timer after a manual reveal
            save_game_state()
            
            await ctx.send(f"✅ Hint **{next_hint_number}** has been manually revealed in {channel.mention}. The timer has been reset.")
        else:
            await ctx.send(f"❌ Error: Hint channel ID {CONFIG['HINT_CHANNEL_ID']} not found. Please check configuration.")
    else:
        await ctx.send(f"❌ Hint **{next_hint_number}** is not configured. Please ensure you have set all {REQUIRED_HINTS} hints.")


@bot.command(name='stop', help='[ADMIN] Forcefully ends the current game and resets ALL game settings.')
@is_authorized_admin()
async def stop_game(ctx):
    global is_game_active, correct_answer, current_hints_revealed, current_hints_storage, last_hint_reveal_time

    # Perform the full reset regardless of the current state of is_game_active
    is_game_active = False
    correct_answer = None
    current_hints_revealed = []
    current_hints_storage = {}
    last_hint_reveal_time = None
    
    if hint_timer.is_running():
        hint_timer.stop()

    save_game_state() # Save cleared state
        
    await ctx.send("🚨 **Game State Forcefully Reset.** All item and hint settings have been cleared. The bot is ready to set up a new game using `!setitem`.")
    await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))


@bot.command(name='status', help='[ADMIN] Displays the current game status and configuration.')
@is_authorized_admin()
async def game_status(ctx):
    """Displays the current game state for admin diagnosis."""
    global is_game_active, correct_answer, hint_timing_minutes, current_hints_storage, last_hint_reveal_time, current_hints_revealed

    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    
    # Game Status Check
    status_emoji = "🟢 ACTIVE" if is_game_active else "🔴 INACTIVE"
    
    # Answer Status Check
    answer_status = f"**{correct_answer}**" if correct_answer else "❌ Not Set"

    # Hint Configuration Status
    configured_hints = len(current_hints_storage)
    hint_status = f"✅ All {REQUIRED_HINTS} hints configured." if configured_hints == REQUIRED_HINTS else f"⚠️ {configured_hints}/{REQUIRED_HINTS} hints configured."

    # Revealed Hints Status
    revealed_count = len(current_hints_revealed)
    revealed_text = f"{revealed_count} / {configured_hints} Revealed."
    
    # Next Hint Time
    next_hint_time_str = "N/A"
    next_hint_time_str_detail = ""
    if is_game_active and last_hint_reveal_time:
        next_reveal = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
        time_until_next = next_reveal - datetime.now()
        
        if time_until_next.total_seconds() > 0:
            seconds = int(time_until_next.total_seconds())
            next_hint_time_str = f"In {format_time_remaining(seconds)}"
            # Use UTC/server time for consistent display
            next_hint_time_str_detail = f"Expected at: {next_reveal.strftime('%H:%M:%S UTC')}" 
        else:
            next_hint_time_str = "⏳ Due now"
            next_hint_time_str_detail = "Waiting for next minute loop."

    # Construct the Embed
    embed = discord.Embed(
        title="🎮 Current Game Status",
        description=f"Status: **{status_emoji}**",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Correct Answer", value=answer_status, inline=False)
    
    # Hint Details
    hint_details = (
        f"**Required:** {REQUIRED_HINTS}\n"
        f"**Configured:** {hint_status}\n"
        f"**Interval:** {hint_timing_minutes} minutes"
    )
    embed.add_field(name="Hint Configuration", value=hint_details, inline=True)
    
    # Timer Details (only if a game is/was active)
    timer_details = (
        f"**Revealed:** {revealed_count} of {REQUIRED_HINTS}\n"
        f"**Last Reveal:** {last_hint_reveal_time.strftime('%H:%M:%S UTC') if last_hint_reveal_time else 'N/A'}\n"
        f"**Next Reveal:** {next_hint_time_str}\n"
        f"{next_hint_time_str_detail if is_game_active and last_hint_reveal_time else ''}"
    )
    embed.add_field(name="Hint Timer", value=timer_details, inline=True)

    await ctx.send(embed=embed)


@bot.command(name='testping', help='[ADMIN] Immediately tests if the bot can ping the configured Hint Role in this channel.')
@is_authorized_admin()
async def test_ping(ctx):
    """Admin command to test role ping functionality immediately, including checks for role existence and hierarchy."""
    
    is_target_channel = ctx.channel.id == CONFIG['HINT_CHANNEL_ID']
    ping_string = generate_hint_ping_string()
    
    # Detailed check for each configured role
    check_results = []
    all_roles_found = True
    
    for role_id in CONFIG['HINT_PING_ROLE_IDS']:
        role = ctx.guild.get_role(role_id)
        
        if role:
            # Check 1: Role found
            role_name = role.name
            
            # Check 2: Hierarchy (Bot's highest role must be above the target role)
            # Find the bot's highest role
            bot_member = ctx.guild.get_member(bot.user.id)
            if not bot_member:
                hierarchy_status = "⚠️ Bot member not found in guild. Cannot check hierarchy."
            else:
                # Get the bot's highest role (highest position)
                bot_highest_role = bot_member.top_role
                
                # Check if bot's role position is higher than the target role's position
                if bot_highest_role.position > role.position:
                    hierarchy_status = "✅ Bot's role is above target role."
                else:
                    hierarchy_status = (
                        "❌ **HIERARCHY ERROR:** Bot's highest role "
                        f"(`{bot_highest_role.name}` at position {bot_highest_role.position}) is **NOT** above the target role "
                        f"(`{role_name}` at position {role.position}). "
                        "**You must move the bot's role higher in the server settings.**"
                    )
            
            check_results.append(
                f"**Role ID: {role_id}**\n"
                f"- Name Found: **{role_name}**\n"
                f"- Hierarchy Check: {hierarchy_status}"
            )
            
        else:
            # Role not found (ID is wrong or bot cache is incomplete)
            all_roles_found = False
            check_results.append(
                f"**Role ID: {role_id}**\n"
                "❌ **ROLE NOT FOUND:** This ID does not correspond to an existing role in the server, or the bot cannot see it. Check the ID."
            )

    # Summarize the findings
    summary = "\n\n".join(check_results)
    
    if not all_roles_found:
        final_status = "❌ **TEST FAILED:** One or more roles were not found."
    elif not ping_string.strip():
        final_status = "❌ **TEST FAILED:** The ping string is empty. Check `CONFIG['HINT_PING_ROLE_IDS']`."
    else:
        final_status = "✅ **PING TEST: CODE GENERATION SUCCESSFUL.**"

    # Warning if not in target channel
    channel_warning = ""
    if not is_target_channel:
        channel_warning = (
            f"⚠️ **Warning:** This test is not running in the configured hint channel ID "
            f"(`{CONFIG['HINT_CHANNEL_ID']}`). "
            f"The final ping will occur in the correct channel when the hint is due."
        )

    test_message = (
        f"{final_status}\n\n"
        f"### Role Check Summary\n"
        f"{summary}\n\n"
        f"### Expected Ping Output\n"
        f"Attempting to ping role(s) with string: `{ping_string.strip()}`\n\n"
        f"**RESULT (If Ping is Active):**\n"
        f"{ping_string} This is a test ping. If you see the role mentioned, the ping works!\n\n"
        f"{channel_warning}"
    )
    
    await ctx.send(test_message)


# --- Game Commands ---
@bot.command(name='start', help='[ADMIN] Starts a new game with the configured item.')
@is_authorized_admin()
async def start_game(ctx):
    global correct_answer, is_game_active, current_hints_revealed, last_hint_reveal_time
    
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    COOLDOWN_MINUTES = CONFIG['GUESS_COOLDOWN_MINUTES']

    if is_game_active:
        await ctx.send("A game is already running! Try guessing with `!guess <item>`.")
        return

    if not correct_answer or len(current_hints_storage) != REQUIRED_HINTS: 
        await ctx.send(f"❌ The administrator must first set the item and all {REQUIRED_HINTS} hints using `!setitem` and `!sethint <1-{REQUIRED_HINTS}> ...` or `!setallhints`")
        return

    is_game_active = True
    current_hints_revealed = []
    
    first_hint_text = current_hints_storage[1]
    last_hint_reveal_time = datetime.now()
    
    # CRITICAL FIX: Ensure the timer is running when starting a new game.
    if not hint_timer.is_running():
        hint_timer.start()
        print("Hint timer restarted via !start command.")

    # Go to the dedicated channel for hints
    announcement_channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])

    if not announcement_channel:
        is_game_active = False # Cancel game start
        await ctx.send("❌ Error: The automatic hint channel was not found. Please ask an admin to check the configuration ID.")
        save_game_state() # Save inactive state
        return

    # Store the first revealed hint and save state
    current_hints_revealed.append({'hint_number': 1, 'text': first_hint_text})
    save_game_state() 

    print(f"New game started, item is {correct_answer}")
    await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))
    
    # Generate ping string and construct the message for the first hint
    ping_string = generate_hint_ping_string()

    start_message = (
        f'{ping_string}📢 **A new item guessing game has started!** Hints will be revealed every **{hint_timing_minutes} minutes**.'
        f'\n\n**First Hint (1/{REQUIRED_HINTS}):** _{first_hint_text}_'
        f'\n\nStart guessing with `!guess <item name>`! (Remember the one guess per {COOLDOWN_MINUTES} minute cooldown.)' # Updated cooldown time
    )
    
    # Send the first hint to the dedicated channel
    await announcement_channel.send(start_message)

    # Acknowledge the start to the admin/caller
    await ctx.send(f"✅ The game has started! The first hint has been sent to {announcement_channel.mention}.")


@bot.command(name='guess', help='Attempts to guess the item name.')
async def guess_item(ctx, *, guess: str):
    global correct_answer, is_game_active, current_hints_revealed, last_hint_reveal_time, user_wins

    if not is_game_active:
        await ctx.send("No active game. Start a new one with `!start`.")
        return
    
    # Check if the command is used in the leaderboard channel (should be caught by global check, but included for robustness)
    if ctx.channel.id == CONFIG['WINS_CHANNEL_ID']:
        await ctx.send("❌ Guessing (`!guess`) is not allowed in this channel. Please use the main game category.", delete_after=10)
        return

    user_id = ctx.author.id
    now = datetime.now()
    cooldown_minutes = CONFIG['GUESS_COOLDOWN_MINUTES']
    
    # Check cooldown
    if user_id in last_guess_time:
        time_since_last_guess = now - last_guess_time[user_id]
        if time_since_last_guess < timedelta(minutes=cooldown_minutes):
            remaining_time = timedelta(minutes=cooldown_minutes) - time_since_last_guess
            seconds = int(remaining_time.total_seconds())
            time_remaining_str = format_time_remaining(seconds)
            
            # Use ctx.reply for better visibility
            await ctx.reply(f"🛑 **Cooldown Active:** You must wait **{time_remaining_str}** before guessing again.", delete_after=5)
            return

    # Record the new guess time *before* checking accuracy
    last_guess_time[user_id] = now
    
    # Check the guess (case-insensitive)
    if not correct_answer:
        # Failsafe for corruption: If the game is active but no answer is set
        await ctx.send("❌ Internal Error: The game is active, but the correct answer is missing. Please ask an admin to run `!stop` to reset the game.")
        return

    # Check the guess (case-insensitive)
    if guess.strip().lower() == correct_answer.lower():
        # --- WINNER LOGIC START (Completed Section) ---
        is_game_active = False
        winner = ctx.author
        winner_mention = winner.mention
        
        # 1. Increment win count and save data
        user_wins[user_id] = user_wins.get(user_id, 0) + 1
        current_wins = user_wins[user_id]
        save_user_wins()

        # 2. Award roles based on the new win count (uses the updated user_wins data)
        await award_winner_roles(winner)
        
        # 3. Announce the winner in the dedicated winner channel
        winner_channel = bot.get_channel(CONFIG['WINNER_ANNOUNCEMENT_CHANNEL_ID'])
        game_end_ping = generate_game_end_ping_string()
        
        if winner_channel:
            await winner_channel.send(
                f"🎉 **WE HAVE A WINNER!** 🎉\n"
                f"{winner_mention} correctly guessed the item: **{correct_answer}**!\n\n"
                f"They now have **{current_wins}** total wins. "
                f"The game is now over. {game_end_ping} - a new item needs to be set up (`!setitem`)."
            )
        else:
            # Fallback announcement in the current channel
             await ctx.send(
                f"🎉 **WE HAVE A WINNER!** 🎉\n"
                f"{winner_mention} correctly guessed the item: **{correct_answer}**! "
                f"The game is now over. {game_end_ping} - a new item needs to be set up (`!setitem`). "
                f"(Warning: Winner channel ID misconfigured.)"
            )
            
        # 4. Reset all game-specific state variables
        correct_answer = None
        current_hints_revealed = []
        current_hints_storage = {}
        last_hint_reveal_time = None
        
        # 5. Stop the hint timer task and save the new, inactive state
        if hint_timer.is_running():
            hint_timer.stop()
        
        save_game_state()
        
        await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
        
        # --- WINNER LOGIC END ---
        return # End the command execution

    else:
        # Incorrect guess feedback
        await ctx.reply("❌ Incorrect guess. Try again once the cooldown is over or a new hint is revealed!", delete_after=5)


# --- Leaderboard Commands ---

@bot.command(name='wins', aliases=['lbc', 'top'], help='Displays the top 10 winners.')
async def show_leaderboard(ctx):
    # Sort users by wins in descending order and take the top 10
    sorted_users = sorted(user_wins.items(), key=lambda item: item[1], reverse=True)
    top_10 = sorted_users[:10]

    if not top_10:
        await ctx.send("The leaderboard is currently empty. Be the first to win!")
        return

    leaderboard_text = ""
    for rank, (user_id, wins) in enumerate(top_10, 1):
        # Fetch member object to get display name
        member = ctx.guild.get_member(user_id)
        # Use fallback if member is not found (e.g., left the guild)
        username = member.display_name if member else f"User ID: {user_id}"
        
        leaderboard_text += f"**#{rank}.** {username}: **{wins}** wins\n"

    embed = discord.Embed(
        title="🏆 Item Guessing Leaderboard - Top 10",
        description=leaderboard_text,
        color=discord.Color.gold()
    )
    embed.set_footer(text="Use !mywins to check your personal stats.")
    
    await ctx.send(embed=embed)


@bot.command(name='mywins', help='Displays your personal win count.')
async def show_my_wins(ctx):
    user_id = ctx.author.id
    wins = user_wins.get(user_id, 0)
    
    await ctx.reply(f"🎉 You have a total of **{wins}** correct guesses (wins)!")

# --- Main Execution ---
if __name__ == '__main__':
    # Get the bot token from environment variables
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN environment variable not set. Exiting.")
        sys.exit(1)

    # Start the Flask keep-alive server in a separate daemon thread
    # This prevents the web server from blocking the Discord bot's execution loop
    print(f"Starting Flask keep-alive server on port {WEB_PORT}...")
    threading.Thread(target=run_flask, daemon=True).start()

    # Run the Discord bot
    print("Starting Discord bot...")
    bot.run(DISCORD_TOKEN)
