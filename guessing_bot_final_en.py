import os
import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import threading
import sys
import asyncio # Důležité pro časování mezi hrami
from flask import Flask

# --- FLASK (WEB SERVICE / KEEP-ALIVE) ---
app = Flask(__name__)
WEB_PORT = os.getenv('PORT', 8080)

@app.route('/')
def home():
    return "Item Guessing Bot Worker is Running! (Keep-Alive Active)", 200

# --- KONFIGURACE ---
CONFIG = {
    'DATA_FILE': 'user_wins.json',
    'GAME_STATE_FILE': 'game_state.json',
    
    'REQUIRED_HINTS': 7,
    'GUESS_COOLDOWN_MINUTES': 30,
    'DEFAULT_HINT_TIMING_MINUTES': 60,

    # *** ZDE DOPLŇ ID SVÝCH KANÁLŮ A ROLÍ ***
    'TARGET_CATEGORY_ID': 1441691009993146490, 
    'WINS_CHANNEL_ID': 1442057049805422693, 
    'WINNER_ANNOUNCEMENT_CHANNEL_ID': 1441858034291708059, 
    'HINT_CHANNEL_ID': 1441386236844572834, 
    
    'ADMIN_ROLE_IDS': [
        1397641683205624009, 
        1441386642332979200 
    ],
    'HINT_PING_ROLE_IDS': [
        1441388270201077882 
    ],
    'GAME_END_PING_ROLE_ID': 1441386642332979200,

    'WINNER_ROLES_CONFIG': {
        1:   1441693698776764486,
        5:   1441693984266129469,
        10:  1441694043477381150,
        25:  1441694109268967505,
        50:  1441694179011989534,
        100: 1441694438345674855
    }
}

# --- Herní proměnné ---
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
is_game_active = False
hint_timing_minutes = CONFIG['DEFAULT_HINT_TIMING_MINUTES']
last_hint_reveal_time = None
current_game_id = None # Sleduje, který slot (1-5) právě běží

# Fronta her (Sloty 1-5)
game_queue = {} 

user_wins = {}
last_guess_time = {}

# Nastavení bota
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Pomocné funkce ---

def format_time_remaining(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "a moment"

def generate_hint_ping_string():
    return "".join([f"<@&{role_id}> " for role_id in CONFIG['HINT_PING_ROLE_IDS']])

def generate_game_end_ping_string():
    return f"<@&{CONFIG['GAME_END_PING_ROLE_ID']}>"

# --- Checks ---

def is_authorized_admin():
    async def predicate(ctx):
        if not ctx.guild: return False
        member_roles = [role.id for role in ctx.author.roles]
        for required_id in CONFIG['ADMIN_ROLE_IDS']:
            if required_id in member_roles: return True
        return False
    return commands.check(predicate)

@bot.check
async def command_location_check(ctx):
    if ctx.guild is None: return True
    if ctx.channel.category_id == CONFIG['TARGET_CATEGORY_ID']: return True
    if ctx.channel.id == CONFIG['WINS_CHANNEL_ID']:
        if ctx.command.name in ['wins', 'lbc', 'top', 'mywins']: return True
        else:
            await ctx.send("This channel is dedicated only to the leaderboard.", delete_after=10)
            return False
    if ctx.command.name == 'testping' and ctx.author.guild_permissions.administrator: return True
    await ctx.send(f"❌ This command can only be used in the designated game category.", delete_after=10)
    return False

# --- Ukládání a načítání dat ---

def load_user_wins():
    global user_wins
    DATA_FILE = CONFIG['DATA_FILE']
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                user_wins = {int(k): v for k, v in data.items()}
        except json.JSONDecodeError:
            user_wins = {}
    else:
        user_wins = {}

def save_user_wins():
    DATA_FILE = CONFIG['DATA_FILE']
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(user_wins, f, indent=4)
    except Exception as e:
        print(f"ERROR SAVING DATA: {e}")

def save_game_state():
    global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time, hint_timing_minutes, game_queue, current_game_id
    
    state = {
        'is_game_active': is_game_active,
        'current_game_id': current_game_id,
        'correct_answer': correct_answer,
        'current_hints_storage': {str(k): v for k, v in current_hints_storage.items()},
        'current_hints_revealed': current_hints_revealed,
        'last_hint_reveal_time': last_hint_reveal_time.isoformat() if last_hint_reveal_time else None,
        'hint_timing_minutes': hint_timing_minutes,
        'game_queue': game_queue
    }
    
    try:
        with open(CONFIG['GAME_STATE_FILE'], 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"ERROR SAVING GAME STATE: {e}")

def load_game_state():
    global correct_answer, current_hints_storage, current_hints_revealed, is_game_active, last_hint_reveal_time, hint_timing_minutes, game_queue, current_game_id
    
    STATE_FILE = CONFIG['GAME_STATE_FILE']
    game_queue = {str(i): {'item': None, 'hints': {}} for i in range(1, 6)}

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                
                is_game_active = state.get('is_game_active', False)
                current_game_id = state.get('current_game_id', None)
                correct_answer = state.get('correct_answer')
                current_hints_storage = {int(k): v for k, v in state.get('current_hints_storage', {}).items()}
                current_hints_revealed = state.get('current_hints_revealed', [])
                hint_timing_minutes = state.get('hint_timing_minutes', CONFIG['DEFAULT_HINT_TIMING_MINUTES'])
                
                loaded_queue = state.get('game_queue', {})
                for i in range(1, 6):
                    key = str(i)
                    if key in loaded_queue:
                        game_queue[key] = loaded_queue[key]
                
                last_time_str = state.get('last_hint_reveal_time')
                if last_time_str:
                    last_hint_reveal_time = datetime.fromisoformat(last_time_str)
                else:
                    last_hint_reveal_time = None
        except json.JSONDecodeError:
            is_game_active = False

# --- Časovač nápověd ---
@tasks.loop(minutes=1)
async def hint_timer():
    global current_hints_revealed, last_hint_reveal_time, current_hints_storage, hint_timing_minutes
    
    if not bot.is_ready() or not is_game_active or not last_hint_reveal_time or not current_hints_storage:
        return
        
    now = datetime.now()
    next_reveal_time = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    
    try:
        if now >= next_reveal_time:
            next_hint_number = len(current_hints_revealed) + 1
            REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
            
            if next_hint_number in current_hints_storage:
                channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
                if channel:
                    hint_text = current_hints_storage[next_hint_number]
                    ping_string = generate_hint_ping_string()
                    ping_message = f"{ping_string}📢 **New Hint ({next_hint_number}/{REQUIRED_HINTS}):** _{hint_text}_"
                    await channel.send(ping_message)
                    current_hints_revealed.append({'hint_number': next_hint_number, 'text': hint_text})
                    last_hint_reveal_time = now
                    save_game_state()
            else:
                if hint_timer.is_running():
                    hint_timer.stop()
    except Exception as e:
        print(f"ERROR in hint_timer task: {e}")

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    load_user_wins()
    load_game_state()
    if is_game_active:
        await bot.change_presence(activity=discord.Game(name=f"Guess the item! (!guess)"))
        if not hint_timer.is_running(): hint_timer.start()
    else:
        await bot.change_presence(activity=discord.Game(name=f"Setting up games (!setitem)"))

# --- Utility Functions ---
async def award_winner_roles(member: discord.Member):
    global user_wins
    user_id = member.id
    guild = member.guild
    WINNER_ROLES_CONFIG = CONFIG['WINNER_ROLES_CONFIG']
    
    user_wins[user_id] = user_wins.get(user_id, 0) + 1
    wins_count = user_wins[user_id]
    save_user_wins()

    achieved_role_id = None
    sorted_wins_levels = sorted(WINNER_ROLES_CONFIG.keys(), reverse=True)
    for level in sorted_wins_levels:
        if wins_count >= level:
            achieved_role_id = WINNER_ROLES_CONFIG[level]
            break

    if achieved_role_id:
        target_role = guild.get_role(achieved_role_id)
        if target_role:
            all_winner_role_ids = list(WINNER_ROLES_CONFIG.values())
            roles_to_remove = [role for role in member.roles if role.id in all_winner_role_ids and role.id != achieved_role_id]
            try:
                if target_role not in member.roles:
                    await member.add_roles(target_role)
                    await member.send(f"You've reached {wins_count} wins and earned the role **{target_role.name}**!")
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)
            except Exception as e:
                print(f"Error managing role: {e}")

# --- ADMIN COMMANDS (QUEUE & CONTROL) ---

@bot.command(name='setitem', help='[ADMIN] Sets the item name for a specific game slot (1-5).')
@is_authorized_admin()
async def set_item_name(ctx, game_id: int, *, item_name: str):
    global game_queue
    if not 1 <= game_id <= 5: return await ctx.send("❌ Game ID must be between 1 and 5.")
    s_id = str(game_id)
    if s_id not in game_queue: game_queue[s_id] = {'item': None, 'hints': {}}
    game_queue[s_id]['item'] = item_name.strip()
    save_game_state()
    await ctx.send(f"✅ Game Slot **#{game_id}**: Item set to **{item_name.strip()}**.")

@bot.command(name='sethint', help='[ADMIN] Sets a hint for a game slot.')
@is_authorized_admin()
async def set_hint(ctx, game_id: int, hint_number: int, *, hint_text: str):
    global game_queue
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    if not 1 <= game_id <= 5: return await ctx.send("❌ Game ID must be between 1 and 5.")
    if not 1 <= hint_number <= REQUIRED_HINTS: return await ctx.send(f"❌ Hint number must be between 1 and {REQUIRED_HINTS}.")
    
    s_id = str(game_id)
    if s_id not in game_queue: game_queue[s_id] = {'item': None, 'hints': {}}
    game_queue[s_id]['hints'][str(hint_number)] = hint_text.strip()
    save_game_state()
    current_count = len(game_queue[s_id]['hints'])
    await ctx.send(f"✅ Game Slot **#{game_id}**: Hint **{hint_number}** set. (Total: {current_count})")

@bot.command(name='setallhints', help='[ADMIN] Bulk set hints for a game slot.')
@is_authorized_admin()
async def set_all_hints(ctx, game_id: int, *, hints_text: str):
    global game_queue
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    if not 1 <= game_id <= 5: return await ctx.send("❌ Game ID must be between 1 and 5.")
    hint_lines = [line.strip() for line in hints_text.split('\n') if line.strip()]
    if len(hint_lines) != REQUIRED_HINTS: return await ctx.send(f"❌ Error: Need exactly **{REQUIRED_HINTS}** hints. Provided {len(hint_lines)}.")
    
    s_id = str(game_id)
    if s_id not in game_queue: game_queue[s_id] = {'item': None, 'hints': {}}
    game_queue[s_id]['hints'] = {}
    for i, hint_text in enumerate(hint_lines, 1):
        game_queue[s_id]['hints'][str(i)] = hint_text
    save_game_state()
    await ctx.send(f"✅ Game Slot **#{game_id}**: All hints set!")

@bot.command(name='queue', help='[ADMIN] Shows the status of all 5 game slots.')
@is_authorized_admin()
async def check_queue(ctx):
    global game_queue, current_game_id, is_game_active
    REQUIRED_HINTS = CONFIG['REQUIRED_HINTS']
    embed = discord.Embed(title="📋 Game Queue Status", color=discord.Color.gold())
    for i in range(1, 6):
        s_id = str(i)
        data = game_queue.get(s_id, {'item': None, 'hints': {}})
        item = data.get('item', "❌ Not Set")
        hint_count = len(data.get('hints', {}))
        
        status_icon = "🔴"
        if item != "❌ Not Set" and hint_count == REQUIRED_HINTS: status_icon = "🟢 Ready"
        elif item != "❌ Not Set": status_icon = "⚠️ No hints"
        if is_game_active and str(current_game_id) == s_id: status_icon = "▶️ **RUNNING**"
        
        embed.add_field(name=f"{status_icon} Slot #{i}", value=f"Item: **{item}**\nHints: {hint_count}/{REQUIRED_HINTS}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='start', help='[ADMIN] Starts a specific game from the queue.')
@is_authorized_admin()
async def start_game(ctx, game_id: int):
    global correct_answer, is_game_active, current_hints_revealed, last_hint_reveal_time, current_hints_storage, current_game_id, game_queue
    
    if is_game_active:
        await ctx.send(f"❌ Game #{current_game_id} is running. Use `!stop` to end it.")
        return

    if not 1 <= game_id <= 5: return await ctx.send("❌ ID must be 1-5.")
    
    s_id = str(game_id)
    slot_data = game_queue.get(s_id)
    if not slot_data or not slot_data.get('item'): return await ctx.send(f"❌ Slot #{game_id} has no item.")
    
    # Load game data
    correct_answer = slot_data['item']
    current_hints_storage = {int(k): v for k, v in slot_data.get('hints', {}).items()}
    
    if len(current_hints_storage) != CONFIG['REQUIRED_HINTS']:
        return await ctx.send(f"❌ Slot #{game_id} is missing hints.")

    is_game_active = True
    current_game_id = game_id
    current_hints_revealed = []
    
    first_hint = current_hints_storage[1]
    last_hint_reveal_time = datetime.now()
    
    if not hint_timer.is_running(): hint_timer.start()

    channel = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
    if channel:
        current_hints_revealed.append({'hint_number': 1, 'text': first_hint})
        save_game_state()
        ping = generate_hint_ping_string()
        await channel.send(f'{ping}📢 **Game #{game_id} Started!**\nHints every {hint_timing_minutes}m.\n\n**Hint 1:** _{first_hint}_')
        await ctx.send(f"✅ Game #{game_id} started!")
    else:
        is_game_active = False
        await ctx.send("❌ Error: Hint channel not found.")

# --- NOVÉ STOP PŘÍKAZY ---

@bot.command(name='stop', help='[ADMIN] Stops/Clears a specific game. usage: !stop <id> or !stop (for active)')
@is_authorized_admin()
async def stop_game(ctx, game_id: int = None):
    global is_game_active, correct_answer, current_hints_revealed, current_game_id, game_queue
    
    # Pokud není zadáno ID, zastaví se aktuální běžící hra (jako "Force Game Over")
    if game_id is None:
        if not is_game_active:
            await ctx.send("❌ No active game to stop. Specify an ID to clear a slot: `!stop <1-5>`.")
            return
        
        is_game_active = False
        correct_answer = None
        current_hints_revealed = []
        if hint_timer.is_running(): hint_timer.stop()
        
        await ctx.send(f"🛑 **Active Game #{current_game_id} Stopped.** (The slot data is still preserved in queue).")
        current_game_id = None
        save_game_state()
        await bot.change_presence(activity=discord.Game(name=f"Waiting for !start"))
        return

    # Pokud je zadáno ID (např. !stop 3) -> vymaže slot a zastaví hru, pokud běží
    if not 1 <= game_id <= 5:
        await ctx.send("❌ Game ID must be between 1 and 5.")
        return
        
    s_id = str(game_id)
    
    # Pokud uživatel ruší hru, která zrovna běží
    if is_game_active and current_game_id == game_id:
        is_game_active = False
        correct_answer = None
        current_hints_revealed = []
        current_game_id = None
        if hint_timer.is_running(): hint_timer.stop()
        await ctx.send(f"🛑 **Active Game #{game_id} forcibly stopped.**")
        await bot.change_presence(activity=discord.Game(name=f"Waiting for !start"))

    # VYMAZÁNÍ DAT SLOTU
    game_queue[s_id] = {'item': None, 'hints': {}}
    save_game_state()
    
    await ctx.send(f"🗑️ **Game Slot #{game_id} has been cleared.** You can now set a new game for this slot.")

@bot.command(name='stopall', help='[ADMIN] Stops EVERYTHING and clears ALL game slots.')
@is_authorized_admin()
async def stop_all_games(ctx):
    global is_game_active, correct_answer, current_hints_revealed, current_game_id, game_queue
    
    # 1. Zastavit aktivní hru
    is_game_active = False
    correct_answer = None
    current_hints_revealed = []
    current_game_id = None
    if hint_timer.is_running(): hint_timer.stop()
    
    # 2. Vymazat celou frontu
    game_queue = {str(i): {'item': None, 'hints': {}} for i in range(1, 6)}
    
    save_game_state()
    
    await ctx.send("🚨🚨 **ALL GAMES STOPPED AND CLEARED.**\nThe entire queue (1-5) is now empty.")
    await bot.change_presence(activity=discord.Game(name=f"Waiting for setup"))

# --- PLAYER COMMANDS & AUTO-START LOGIC ---

@bot.command(name='guess', help='Attempts to guess the item name.')
async def guess_item(ctx, *, guess: str):
    global correct_answer, is_game_active, current_game_id, current_hints_revealed, current_hints_storage, game_queue

    if not is_game_active:
        await ctx.send("No active game. Ask admin to `!start` one.")
        return
    
    if ctx.channel.id == CONFIG['WINS_CHANNEL_ID']:
        await ctx.send("❌ Guessing not allowed in leaderboard channel.", delete_after=5)
        return

    user_id = ctx.author.id
    now = datetime.now()
    cooldown = CONFIG['GUESS_COOLDOWN_MINUTES']
    
    if user_id in last_guess_time:
        diff = now - last_guess_time[user_id]
        if diff < timedelta(minutes=cooldown):
            rem = int((timedelta(minutes=cooldown) - diff).total_seconds())
            await ctx.reply(f"🛑 Cooldown! Wait **{format_time_remaining(rem)}**.", delete_after=5)
            return

    last_guess_time[user_id] = now
    
    # LOGIKA VÝHRY
    if guess.strip().lower() == correct_answer.lower():
        await ctx.send(f"🎉 **{ctx.author.display_name}** guessed it! The item was: **{correct_answer}**!")
        
        announcement_channel = bot.get_channel(CONFIG['WINNER_ANNOUNCEMENT_CHANNEL_ID'])
        if announcement_channel:
            await announcement_channel.send(f"🏆 **WINNER!** {ctx.author.mention} guessed the item: **{correct_answer}** (Game #{current_game_id})!")
        
        if hint_timer.is_running(): hint_timer.stop()
        await award_winner_roles(ctx.author)

        finished_game_id = current_game_id

        # Reset aktivní hry
        is_game_active = False
        correct_answer = None
        current_hints_revealed = []
        current_game_id = None
        save_game_state()
        
        await ctx.send(f"{generate_game_end_ping_string()} ✅ Game Over! Item found.")

        # --- AUTOMATICKÝ START DALŠÍ HRY ---
        next_id = finished_game_id + 1
        next_id_str = str(next_id)

        # Zkontrolujeme, zda další slot existuje a má nastavený Item
        if next_id_str in game_queue and game_queue[next_id_str]['item']:
            await ctx.send(f"⏳ **Next Game Detected!** Starting Game #{next_id} in 15 seconds...")
            await asyncio.sleep(15) 
            
            # Znovu zkontrolujeme (kdyby někdo mezitím dal !stopall)
            if next_id_str in game_queue and game_queue[next_id_str]['item']:
                 await start_game(ctx, next_id)
            else:
                 await ctx.send("⚠️ Auto-start cancelled: Game slot was cleared.")
        else:
            await ctx.send("🏁 **Queue Finished.** No more prepared games found.")
            
    else:
        cd_str = format_time_remaining(cooldown * 60)
        await ctx.send(f"❌ Wrong, **{ctx.author.display_name}**. Try again in {cd_str}.")

# --- OSTATNÍ PŘÍKAZY ---

@bot.command(name='sethinttiming')
@is_authorized_admin()
async def set_hint_timing(ctx, minutes: int):
    global hint_timing_minutes
    if not 1 <= minutes <= 60: return await ctx.send("1-60 mins only.")
    hint_timing_minutes = minutes
    save_game_state()
    await ctx.send(f"✅ Interval: {minutes} mins.")

@bot.command(name='revealhint')
@is_authorized_admin()
async def reveal_hint_manual(ctx):
    global current_hints_revealed, last_hint_reveal_time
    if not is_game_active: return await ctx.send("No game active.")
    nxt = len(current_hints_revealed) + 1
    if nxt > CONFIG['REQUIRED_HINTS']: return await ctx.send("All revealed.")
    
    if nxt in current_hints_storage:
        ch = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
        if ch:
            txt = current_hints_storage[nxt]
            ping = generate_hint_ping_string()
            await ch.send(f"{ping}📢 **Manual Hint ({nxt}):** _{txt}_")
            current_hints_revealed.append({'hint_number': nxt, 'text': txt})
            last_hint_reveal_time = datetime.now()
            save_game_state()
            await ctx.send(f"✅ Hint {nxt} revealed.")

@bot.command(name='current')
async def show_current_hints(ctx):
    if not is_game_active: return await ctx.send("No active game.")
    embed = discord.Embed(title=f"Hints ({len(current_hints_revealed)}/{CONFIG['REQUIRED_HINTS']})", color=discord.Color.teal())
    for h in current_hints_revealed: embed.add_field(name=f"#{h['hint_number']}", value=h['text'], inline=False)
    await ctx.send(embed=embed)

@bot.command(name='nexthint')
async def show_next_hint_time(ctx):
    if not is_game_active: return await ctx.send("No active game.")
    if len(current_hints_revealed) >= CONFIG['REQUIRED_HINTS']: return await ctx.send("All hints revealed.")
    nxt = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    rem = int((nxt - datetime.now()).total_seconds())
    await ctx.send(f"⏳ Next hint: {format_time_remaining(rem) if rem > 0 else 'Soon'}")

@bot.command(name='wins')
async def leaderboard(ctx):
    if not user_wins: return await ctx.send("No wins yet.")
    sorted_wins = sorted(user_wins.items(), key=lambda x: x[1], reverse=True)[:10]
    desc = "\n".join([f"**{i+1}.** <@{uid}> - {count}" for i, (uid, count) in enumerate(sorted_wins)])
    await ctx.send(embed=discord.Embed(title="Top 10", description=desc, color=discord.Color.gold()))

@bot.command(name='mywins')
async def my_wins(ctx):
    await ctx.send(f"You have {user_wins.get(ctx.author.id, 0)} wins.")

@bot.command(name='status')
@is_authorized_admin()
async def game_status(ctx):
    if not is_game_active: return await ctx.send("🔴 No game active.")
    embed = discord.Embed(title=f"Active: Slot #{current_game_id}", color=discord.Color.green())
    embed.add_field(name="Item", value=f"||{correct_answer}||")
    await ctx.send(embed=embed)

@bot.command(name='testping')
@is_authorized_admin()
async def test_ping(ctx):
    await ctx.send(f"Ping test: {generate_hint_ping_string()}")

# --- STARTUP ---
def run_flask():
    app.run(host='0.0.0.0', port=int(WEB_PORT))

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN: print("Error: DISCORD_TOKEN missing.")
    else: bot.run(TOKEN)
