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
	'DEFAULT_HINT_TIMING_MINUTES': 60, # Initial value, modified by !sethinttiming

	# Channel and Category IDs (***UPDATE THESE PLACEHOLDERS***)
	'TARGET_CATEGORY_ID': 1441691009993146490, # Main game category ID
	'WINS_CHANNEL_ID': 1442057049805422693, 	# Channel for !wins command only
	'WINNER_ANNOUNCEMENT_CHANNEL_ID': 1441858034291708059, # Channel for announcing the winner
	'HINT_CHANNEL_ID': 1441386236844572834, 	# Channel for periodic hint announcements
	
	# Role IDs (UPDATED/CONFIRMED BASED ON USER REQUEST)
	'ADMIN_ROLE_IDS': [
		1397641683205624009, # Admin: Support Team
		1441386642332979200 # Admin: Host
	],
	# Role to ping on every new hint reveal
	'HINT_PING_ROLE_IDS': [
		1441388270201077882 # Ping Role
	],
	# Role to ping when the game ends and the queue is empty
	'GAME_END_PING_ROLE_ID': 1441386642332979200, # Host role

	# Winner Roles (Key: minimum wins required, Value: Role ID)
	# (These IDs must be updated by the user for their server roles)
	'WINNER_ROLES_CONFIG': {
		1: 	 1441693698776764486,
		5: 	 1441693984266129469,
		10: 	1441694043477381150,
		25: 	1441694109268967505,
		50: 	1441694179011989534,
		100: 	1441694438345674855
	}
}

# --- Game State Variables ---
# State for the CURRENTLY active game
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
is_game_active = False

# NEW: State for the queue of upcoming games
# Structure: [{ 'item_name': str, 'hints_storage': {1: 'h1', ...} }, ...]
game_queue = [] 

# Timing/Cooldown state
hint_timing_minutes = CONFIG['DEFAULT_HINT_TIMING_MINUTES'] 
last_hint_reveal_time = None
user_wins = {}
last_guess_time = {} 

# Set up Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
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
	return pings

def generate_game_end_ping_string():
	"""Generates the ping string for the single game end role."""
	role_id = CONFIG['GAME_END_PING_ROLE_ID']
	ping = f"<@&{role_id}>"
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

# --- Global Command Location Check (Unchanged) ---

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

# --- Data Persistence Functions (User Wins) (Unchanged) ---
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

# --- Game State Persistence Functions (UPDATED) ---
def save_game_state():
	"""Saves the critical game state variables, including the queue, to a JSON file."""
	global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time, hint_timing_minutes, game_queue
	
	# Prepare the state for JSON serialization
	state = {
		'is_game_active': is_game_active,
		'correct_answer': correct_answer,
		# Convert keys of current_hints_storage (int) to strings for JSON
		'current_hints_storage': {str(k): v for k, v in current_hints_storage.items()},
		'current_hints_revealed': current_hints_revealed,
		# Convert datetime object to ISO 8601 string for persistence
		'last_hint_reveal_time': last_hint_reveal_time.isoformat() if last_hint_reveal_time else None,
		'hint_timing_minutes': hint_timing_minutes,
		
		# NEW: Save the game queue
		'game_queue': game_queue
	}
	
	try:
		with open(CONFIG['GAME_STATE_FILE'], 'w') as f:
			json.dump(state, f, indent=4)
			print("Game state saved.")
	except Exception as e:
		print(f"ERROR SAVING GAME STATE: {e}")

def load_game_state():
	"""Loads the game state, including the queue, from a JSON file."""
	global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time, hint_timing_minutes, game_queue
	
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
					
				# NEW: Load the game queue
				game_queue = state.get('game_queue', [])

				print(f"Game state loaded. Active: {is_game_active}, Queue size: {len(game_queue)}")
				
		except json.JSONDecodeError:
			print("ERROR: game_state.json is corrupted or empty. Starting fresh.")
			is_game_active = False
			game_queue = [] # Reset queue on error
	
# --- END Game State Persistence Functions ---


# --- Timed Hint Task (Unchanged logic) ---
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
					ping_string = generate_hint_ping_string()
					
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

# --- Bot Events (Minor update to presence) ---
@bot.event
async def on_ready():
	print(f'{bot.user.name} has connected to Discord!')
	load_user_wins()
	load_game_state() # Load game state on startup
	
	if is_game_active:
		await bot.change_presence(activity=discord.Game(name=f"Guess the item! ({len(game_queue)} queued)"))
		print(f"Resuming active game for item: {correct_answer}")
	else:
		queue_count = len(game_queue)
		if queue_count > 0:
			await bot.change_presence(activity=discord.Game(name=f"Ready! ({queue_count} queued) !start"))
		else:
			await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
		
	# CRITICAL FIX: Ensure timer starts on ready based on the loaded state
	if is_game_active and not hint_timer.is_running():
		hint_timer.start()
		print("Hint timer started/restarted on bot startup.")
	elif not is_game_active and hint_timer.is_running():
		hint_timer.stop()
		print("Hint timer stopped on startup: No active game.")


# --- Utility Function for Automatic Game Start ---
async def start_next_game_in_queue(channel: discord.TextChannel, winner_mention: str = None):
	"""
	Automatically loads and starts the next game from the queue.
	Called after a win or a manual stop/skip.
	"""
	global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time
	
	REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
	COOLDOWN_MINUTES = CONFIG['GUESS_COOLDOWN_MINUTES']
	
	if not game_queue:
		# If the queue is empty, the game is truly over
		is_game_active = False
		save_game_state()
		if hint_timer.is_running():
			hint_timer.stop()
			
		game_end_ping_string = generate_game_end_ping_string()
		message = (
			f"🎉 **Game Ended!** The queue is empty. "
			f"{game_end_ping_string} An admin must set up the next game using `!setitem`."
		)
		await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))
		await channel.send(message)
		return
	
	# Load the next game from the queue
	next_game = game_queue.pop(0) 
	
	# Set active state variables
	correct_answer = next_game['item_name']
	current_hints_storage = next_game['hints_storage']
	is_game_active = True
	current_hints_revealed = []
	
	first_hint_text = current_hints_storage[1]
	last_hint_reveal_time = datetime.now() # Reset timer
	
	# Ensure the timer is running
	if not hint_timer.is_running():
		hint_timer.start()

	# Announce the start in the dedicated hint channel
	ping_string = generate_hint_ping_string()
	queue_count = len(game_queue)
	queue_status_text = f"({queue_count} more game{'s' if queue_count != 1 else ''} queued)"

	start_message = (
		f'{ping_string}📢 **New Game Auto-Start!** {queue_status_text} Hints will reveal every **{hint_timing_minutes} minutes**.'
		f'\n\n**First Hint (1/{REQUIRED_HINTS}):** _{first_hint_text}_'
		f'\n\nStart guessing with `!guess <item name>`! (Cooldown: {format_time_remaining(COOLDOWN_MINUTES * 60)})'
	)
	
	# Store the first revealed hint and save state
	current_hints_revealed.append({'hint_number': 1, 'text': first_hint_text})
	save_game_state() 

	# Update presence and announce
	await bot.change_presence(activity=discord.Game(name=f"Guess the item! ({queue_count} queued)"))
	await channel.send(start_message)

# --- Utility Functions (Role Awarding - Unchanged) ---
async def award_winner_roles(member: discord.Member):
	global user_wins

	user_id = member.id
	guild = member.guild
	WINNER_ROLES_CONFIG = CONFIG['WINNER_ROLES_CONFIG']
	
	# 1. Update and save win count
	user_wins[user_id] = user_wins.get(user_id, 0) + 1
	wins_count = user_wins[user_id]
	save_user_wins()

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
			if target_role not in member.roles:
				await member.add_roles(target_role)
				await member.send(f"You've reached {wins_count} wins and earned the role **{target_role.name}**!")
			
			# Remove redundant lower-tier roles
			if roles_to_remove:
				await member.remove_roles(*roles_to_remove)
				
		except discord.Forbidden:
			print(f"Permission Error: Cannot add/remove role for {member.display_name}. Check bot permissions and role hierarchy.")
		except Exception as e:
			print(f"Error managing role: {e}")


# --- Admin Commands (UPDATED for Queue) ---

def get_or_create_next_game(item_name: str = None):
	"""
	Finds the last game in the queue that is not fully set up (missing item or hints),
	or creates a new placeholder game if needed.
	Returns (game_object, index_in_queue)
	"""
	REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
	
	# 1. Look for an incomplete game at the end of the queue
	if game_queue and (not game_queue[-1]['item_name'] or len(game_queue[-1]['hints_storage']) < REQUIRED_HINTS):
		return game_queue[-1], len(game_queue) - 1
		
	# 2. If all games are complete, or queue is empty, create a new game placeholder
	new_game = {
		'item_name': item_name.strip() if item_name else None,
		'hints_storage': {}
	}
	game_queue.append(new_game)
	return new_game, len(game_queue) - 1


@bot.command(name='setitem', help='[ADMIN] Sets the correct item name for the NEXT game in the queue.')
@is_authorized_admin()
async def set_item_name(ctx, *, item_name: str):
	global game_queue
	
	# Find or create the placeholder for the next game
	game, index = get_or_create_next_game(item_name=item_name)
	
	# If the item was already set on the placeholder, update it
	is_new_game = index == len(game_queue) - 1 and not game['item_name']
	
	game['item_name'] = item_name.strip()
	
	save_game_state() # Save state after setting item

	if is_new_game:
		await ctx.send(f"✅ Game **#{index + 1}** queued! Item set to: **{item_name.strip()}**. Now add hints.")
	else:
		await ctx.send(f"✅ Game **#{index + 1}** item updated to: **{item_name.strip()}**.")


@bot.command(name='sethint', help=f"[ADMIN] Sets a hint for the LAST item in the queue. Usage: !sethint 1 This is the first hint...")
@is_authorized_admin()
async def set_hint(ctx, number: int, *, hint_text: str):
	global game_queue

	REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']

	# Find the last game in the queue
	if not game_queue:
		return await ctx.send("❌ Please set the item first using `!setitem <item_name>` before setting hints.")
		
	game = game_queue[-1]
	
	if not game['item_name']:
		return await ctx.send("❌ The item name for the last queued game is missing. Use `!setitem` first.")

	if not 1 <= number <= REQUIRED_HINTS: 
		await ctx.send(f"❌ Hint number must be between 1 and {REQUIRED_HINTS}.")
		return

	game['hints_storage'][number] = hint_text.strip()
	
	current_count = len(game['hints_storage'])
	
	# Announce the current number of configured hints
	if current_count == REQUIRED_HINTS:
		# Sort the hints to ensure they are sequential before declaring complete
		if all(i in game['hints_storage'] for i in range(1, REQUIRED_HINTS + 1)):
			save_game_state() # Save state when fully configured
			await ctx.send(
				f"✅ Hint No. **{number}/{REQUIRED_HINTS}** set. **Game #{len(game_queue)} is now fully configured and ready!**"
			)
			if not is_game_active:
				await bot.change_presence(activity=discord.Game(name=f"Ready! ({len(game_queue)} queued) !start"))
			return # Exit after full config message
		
	await ctx.send(f"✅ Hint No. **{number}/{REQUIRED_HINTS}** has been set for Game #{len(game_queue)}. Configured hints: **{current_count}/{REQUIRED_HINTS}**.")
	save_game_state()


@bot.command(name='setallhints', help=f'[ADMIN] Sets all {CONFIG["REQUIRED_HINTS"]} hints at once for the LAST item in the queue.')
@is_authorized_admin()
async def set_all_hints(ctx, *, hints_text: str):
	global game_queue

	REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']

	if not game_queue:
		return await ctx.send("❌ Please set the item first using `!setitem <item_name>` before setting hints.")
		
	game = game_queue[-1]
	
	if not game['item_name']:
		return await ctx.send("❌ The item name for the last queued game is missing. Use `!setitem` first.")

	hint_lines = [line.strip() for line in hints_text.split('\n') if line.strip()]

	if len(hint_lines) != REQUIRED_HINTS: 
		return await ctx.send(
			f"❌ Error: You must provide exactly **{REQUIRED_HINTS}** hints, one per line. "
			f"You provided {len(hint_lines)}. Please ensure you use a multi-line code block in Discord."
		)
	
	# Clear existing hints and set the new ones
	game['hints_storage'] = {}
	for i, hint_text in enumerate(hint_lines, 1):
		game['hints_storage'][i] = hint_text

	save_game_state() 

	await ctx.send(
		f"✅ Game **#{len(game_queue)}** successfully set with **all {REQUIRED_HINTS} hints** at once! The game is ready to start."
	)
	if not is_game_active:
		await bot.change_presence(activity=discord.Game(name=f"Ready! ({len(game_queue)} queued) !start"))


@bot.command(name='queue', help='[ADMIN] Shows the list of upcoming games in the queue.')
@is_authorized_admin()
async def show_queue(ctx):
	global game_queue
	
	REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
	
	if not game_queue:
		return await ctx.send("The game queue is currently empty. Use `!setitem` to add a new game.")
		
	embed = discord.Embed(
		title="⏭️ Upcoming Game Queue",
		description=f"Total games queued: **{len(game_queue)}**",
		color=discord.Color.gold()
	)
	
	for i, game in enumerate(game_queue):
		item_name = game['item_name'] or "*(Item Name Missing)*"
		hints_count = len(game['hints_storage'])
		
		status = "✅ READY"
		if not game['item_name']:
			status = "❌ ITEM MISSING"
		elif hints_count < REQUIRED_HINTS:
			status = f"⚠️ HINTS ({hints_count}/{REQUIRED_HINTS})"
			
		embed.add_field(
			name=f"Game #{i + 1} ({status})",
			value=f"Item: `{item_name}`",
			inline=False
		)
		
	await ctx.send(embed=embed)


@bot.command(name='start', help='[ADMIN] Starts the next game from the queue immediately.')
@is_authorized_admin()
async def start_game(ctx):
	global is_game_active
	
	if is_game_active:
		await ctx.send("A game is already running! You must use `!stop` or wait for the current game to finish.")
		return

	if not game_queue:
		return await ctx.send("❌ The game queue is empty. Please set the item and hints first.")

	# Get the dedicated channel for the announcement
	announcement_channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
	if not announcement_channel:
		return await ctx.send("❌ Error: The automatic hint channel was not found. Please ask an admin to check the configuration ID.")

	# Use the utility function to load and start the first game in the queue
	await start_next_game_in_queue(announcement_channel)
	
	# Acknowledge the start to the admin/caller
	await ctx.send(f"✅ The game has started using the first game from the queue! The first hint has been sent to {announcement_channel.mention}.")


@bot.command(name='stop', help='[ADMIN] Forcefully ends the current game and clears the ENTIRE queue.')
@is_authorized_admin()
async def stop_game(ctx):
	global is_game_active, correct_answer, current_hints_revealed, current_hints_storage, last_hint_reveal_time, game_queue

	# Perform the full reset 
	is_game_active = False
	correct_answer = None
	current_hints_revealed = []
	current_hints_storage = {}
	last_hint_reveal_time = None
	game_queue = [] # Clear the queue
	
	if hint_timer.is_running():
		hint_timer.stop()

	save_game_state() # Save cleared state
		
	await ctx.send("🚨 **Game State Forcefully Reset.** The active game and the entire game queue have been cleared. The bot is ready to set up a new game using `!setitem`.")
	await bot.change_presence(activity=discord.Game(name=f"Setting up the game (!setitem)"))

@bot.command(name='skip', help='[ADMIN] Skips the current active game and starts the next one in the queue, if available.')
@is_authorized_admin()
async def skip_game(ctx):
	global is_game_active, correct_answer, current_hints_revealed, current_hints_storage, last_hint_reveal_time
	
	if not is_game_active:
		return await ctx.send("❌ No game is currently active to skip. Use `!start` to begin the first game in the queue.")
	
	# Clear the active game state
	correct_answer = None
	current_hints_revealed = []
	current_hints_storage = {}
	last_hint_reveal_time = None
	
	announcement_channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
	if not announcement_channel:
		# If we can't announce, we just stop.
		is_game_active = False
		save_game_state()
		return await ctx.send("❌ Error: Cannot find the hint channel. Game stopped and reset. Please fix the config.")
	
	await ctx.send("⏭️ **Skipping current game...** Loading next game from the queue.")
	
	# Automatically start the next game
	await start_next_game_in_queue(announcement_channel)


# --- Game Commands (UPDATED Win Logic) ---

@bot.command(name='guess', help='Attempts to guess the item name.')
async def guess_item(ctx, *, guess: str):
	global correct_answer, is_game_active

	if not is_game_active:
		await ctx.send("No active game. Start a new one with `!start` or wait for the queue to start the next one.")
		return
	
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
			
			await ctx.reply(f"🛑 **Cooldown Active:** You must wait **{time_remaining_str}** before guessing again.", delete_after=5)
			return

	# Record the new guess time *before* checking accuracy
	last_guess_time[user_id] = now
	
	if not correct_answer:
		await ctx.send("❌ Internal Error: The game is active, but the correct answer is missing. Please ask an admin to run `!stop` to reset the game.")
		return

	# Check the guess (case-insensitive)
	if guess.strip().lower() == correct_answer.lower():
		# --- WIN CONDITION MET ---
		
		# 1. Announce in the current channel
		await ctx.send(f"🎉 **Congratulations, {ctx.author.display_name}!** You guessed the item: **{correct_answer}**! The game is over!")

		# 2. Announce in the dedicated winner channel
		announcement_channel = bot.get_channel(CONFIG['WINNER_ANNOUNCEMENT_CHANNEL_ID'])
		hint_channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])

		if announcement_channel:
			winner_ping = ctx.author.mention
			message = f"🏆 **ROUND WINNER!** {winner_ping} just guessed the item. The correct answer was: **{correct_answer}**!"
			await announcement_channel.send(message)
		
		await award_winner_roles(ctx.author)

		# Reset current game variables
		current_item_guessed = correct_answer
		correct_answer = None 
		current_hints_revealed = []
		current_hints_storage = {}
		
		# 3. Check the queue and start the next game automatically
		if hint_channel:
			await start_next_game_in_queue(hint_channel)
		else:
			# If hint channel is missing, just stop everything
			is_game_active = False
			save_game_state()
			if hint_timer.is_running():
				hint_timer.stop()
			await ctx.send("❌ **Game won, but critical channel missing.** Game stopped. An admin must check configuration.")

	else:
		# Show cooldown time in the message
		cooldown_display = format_time_remaining(CONFIG['GUESS_COOLDOWN_MINUTES'] * 60)
		await ctx.send(f"❌ Wrong! **{ctx.author.display_name}**, that's not it. You can guess again in {cooldown_display}.")

# --- Game Commands (Unchanged logic for status/hints) ---

@bot.command(name='current', help='Displays the hints revealed so far.')
async def show_current_hints(ctx):
	"""Displays the hints revealed so far, or the game status if no hints are out."""
	global is_game_active, current_hints_revealed

	if not is_game_active:
		await ctx.send(f"No game is currently active. The queue size is **{len(game_queue)}**.")
		return
	
	if not current_hints_revealed:
		await ctx.send("The game has started, but no hints have been revealed yet (waiting for the first hint to be posted).")
		return

	REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
	
	embed = discord.Embed(
		title=f"🔎 Current Game Hints ({len(current_hints_revealed)}/{REQUIRED_HINTS})",
		color=discord.Color.teal()
	)
	
	for hint in current_hints_revealed:
		embed.add_field(name=f"Hint {hint['hint_number']}", value=f"_{hint['text']}_", inline=False)

	await ctx.send(embed=embed)


@bot.command(name='nexthint', help='Shows the time remaining until the next hint is revealed.')
async def show_next_hint_time(ctx):
	"""Shows the time remaining until the next hint is revealed."""
	global is_game_active, last_hint_reveal_time, hint_timing_minutes, current_hints_revealed, current_hints_storage

	if not is_game_active:
		return await ctx.send("The guessing game is currently inactive. Use `!start` to begin a new round.")

	# Check if all hints have been revealed (and the timer should be stopped)
	if len(current_hints_revealed) == CONFIG['REQUIRED_HINTS'] or len(current_hints_revealed) == len(current_hints_storage):
		return await ctx.send("All hints have already been revealed for the current item! Time to guess!")
	
	if not last_hint_reveal_time:
		return await ctx.send("Game is active, but the hint timer hasn't officially started (usually fixed by `!start`).")

	# Calculate the next reveal time
	next_reveal = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
	time_until_next = next_reveal - datetime.now()
	seconds = int(time_until_next.total_seconds())
	
	if seconds <= 0:
		# Time has passed, but the hint_timer loop hasn't run yet.
		await ctx.send("⏳ The next hint is due now and will be revealed momentarily (waiting for the minute-long timer loop to execute).")
	else:
		time_remaining_str = format_time_remaining(seconds)
		next_hint_number = len(current_hints_revealed) + 1
		
		await ctx.send(
			f"🕐 The next hint (**{next_hint_number}/{CONFIG['REQUIRED_HINTS']}**) will be revealed in approximately **{time_remaining_str}**."
		)

# [Remaining commands like !wins, !lbc, etc., would go here if they were provided in the input, but since they weren't, the file ends here with a placeholder]

# --- Bot Run Command (Placeholder for completeness) ---

# if __name__ == '__main__':
# 	# NOTE: The provided code snippet did not include the final run block,
# 	# but it is necessary for a working bot. This block is for context.
# 	# The actual hosting environment (like Render) handles the running.
# 	# The Flask server runs on a separate thread/process managed by the host.

# 	# bot.run(os.getenv('DISCORD_TOKEN'))
# 	pass
