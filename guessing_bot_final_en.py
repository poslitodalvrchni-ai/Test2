import os
import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
import threading
import asyncio
import sys
from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
import random

# --- FLASK (KEEP-ALIVE) ---
app = Flask(__name__)
WEB_PORT = os.getenv('PORT', 8080)

@app.route('/')
def home():
    return "Bot & MongoDB Worker Running!", 200

# --- KONFIGURACE ---
CONFIG = {
    'REQUIRED_HINTS': 7,
    'GUESS_COOLDOWN_MINUTES': 30,
    'DEFAULT_HINT_TIMING_MINUTES': 60,
    
    # DATABÁZE
    'DB_NAME': 'discord_game_bot',

    # KANÁLY A ROLE (Zkopírováno z tvého zadání)
    'TARGET_CATEGORY_ID': 1441691009993146490, 
    'WINS_CHANNEL_ID': 1442057049805422693, 
    'WINNER_ANNOUNCEMENT_CHANNEL_ID': 1441858034291708059, 
    'HINT_CHANNEL_ID': 1441386236844572834, 
    'ACHIEVEMENT_CHANNEL_ID': 1457293868876955785,

    'ADMIN_ROLE_IDS': [1397641683205624009, 1441386642332979200],
    'HINT_PING_ROLE_IDS': [1441388270201077882],
    'GAME_END_PING_ROLE_ID': 1441386642332979200,

    'WINNER_ROLES_CONFIG': {
        1: 1441693698776764486, 5: 1441693984266129469, 10: 1441694043477381150,
        25: 1441694109268967505, 50: 1441694179011989534, 100: 1441694438345674855
    }
}

# --- DEFINICE ACHIEVEMENTŮ (Zkráceno pro přehlednost, logika zůstává) ---
# (V kódu jsou všechny achievementy, které jsi poslal v Opravě 2)
ACHIEVEMENTS = {
    'first_blood': {'name': 'Feels good right? 🩸', 'desc': 'Win your first game.', 'cat': 'General'},
    'persistent': {'name': 'Persistent 🔨', 'desc': 'Submit a total of 50 guesses.', 'cat': 'General', 'target': 50},
    'good_luck': {'name': 'Good Luck 🍀', 'desc': 'Use the !pray command.', 'cat': 'General'},
    'loyalist': {'name': 'Loyalist 🎖️', 'desc': 'Participate in 10 different games.', 'cat': 'General', 'target': 10},
    'addict': {'name': 'Addict 🧪', 'desc': 'Submit a total of 100 guesses.', 'cat': 'General', 'target': 100},
    'veteran': {'name': 'Veteran 🏅', 'desc': 'Reach a total of 25 wins.', 'cat': 'General', 'target': 25},
    'legend': {'name': 'Legend 👑', 'desc': 'Reach a total of 100 wins.', 'cat': 'General', 'target': 100},
    
    'speedrunner': {'name': 'Speedrunner ⚡', 'desc': 'Guess within 1 minute of Hint 1.', 'cat': 'Skill'},
    'sniper': {'name': 'Sniper 🎯', 'desc': 'Guess correctly immediately after Hint 1.', 'cat': 'Skill'},
    'getting_there': {'name': 'Getting There 📈', 'desc': 'Guess incorrectly 5+ times in one game before winning.', 'cat': 'Skill'},
    'too_late': {'name': 'Too Late ⏰', 'desc': 'Guess incorrectly within 7.5s after someone won.', 'cat': 'Skill'},
    'calc_risk': {'name': 'Calculated Risk 🎲', 'desc': 'Win before Hint 4 is revealed.', 'cat': 'Skill'},
    'double_down': {'name': 'Double Down ✌️', 'desc': 'Win two games in a row.', 'cat': 'Skill'},
    'clutch': {'name': 'Clutch 🧤', 'desc': 'Guess within 60s before the next hint drops.', 'cat': 'Skill'},
    'triple_threat': {'name': 'Triple Threat ☘️', 'desc': 'Win 3 games in a single day.', 'cat': 'Skill'},
    'last_second': {'name': 'Last Second ⌛', 'desc': 'Guess within the last 5 minutes of a hint cycle.', 'cat': 'Skill'},
    'sharp_eye': {'name': 'Sharp Eye 👁️', 'desc': 'Win within 10s of any hint being posted.', 'cat': 'Skill'},
    'finisher': {'name': 'The Finisher 🏁', 'desc': 'Win a game where all 7 hints were revealed.', 'cat': 'Skill'},
    'brute_force': {'name': 'Brute Force 🦾', 'desc': 'Guess 10 different items in a single game.', 'cat': 'Skill'},
    'early_bird': {'name': 'Early Bird 🌅', 'desc': 'Be the first person to guess in a new game.', 'cat': 'Skill'},
    'close_call': {'name': 'Close Call 🤏', 'desc': 'Guess within 2 minutes of game start.', 'cat': 'Skill'},

    'workaholic': {'name': 'Workaholic 💼', 'desc': 'Submit 20 guesses in a single game.', 'cat': 'Grind'},
    'dedication': {'name': 'Dedication ❤️', 'desc': 'Submit a guess in 5 consecutive games.', 'cat': 'Grind'},
    'no_rest': {'name': 'No Rest ☕', 'desc': 'Win a game, then verify participation in next.', 'cat': 'Grind'},
    'halfway': {'name': 'Halfway There 🚩', 'desc': 'Unlock 50% of achievements.', 'cat': 'Grind'},

    'again': {'name': 'Again....? 🔄', 'desc': 'Guess the exact same incorrect item 5 times in one game.', 'cat': 'Secret', 'secret': True},
    'ghost': {'name': 'Ghost Guesser 👻', 'desc': 'Use !guess when no game is active.', 'cat': 'Secret', 'secret': True},
    'speed_limit': {'name': 'Speed Limit 🛑', 'desc': 'Try to guess 3 times while on cooldown.', 'cat': 'Secret', 'secret': True},
    'copycat': {'name': 'Copycat 🐈', 'desc': 'Guess the same thing as someone else within 5s.', 'cat': 'Secret', 'secret': True},
    'maybe': {'name': 'Maybe? ❓', 'desc': 'Guess the answer from the previous game.', 'cat': 'Secret', 'secret': True},
    'philosopher': {'name': 'The Philosopher 🌍', 'desc': 'Guess "life", "everything", or "word".', 'cat': 'Secret', 'secret': True},
    'keyboard': {'name': 'Keyboard Warrior ⌨️', 'desc': 'Send a guess longer than 15 chars.', 'cat': 'Secret', 'secret': True},
    'self_check': {'name': 'Self-Check 🧐', 'desc': 'Use !mywins 3 times in 10s.', 'cat': 'Secret', 'secret': True},
    'impatience': {'name': 'Impatience 💢', 'desc': 'Use !nexthint 5 times in one game.', 'cat': 'Secret', 'secret': True},
    'silent': {'name': 'Silent Winner 🤫', 'desc': 'Win without typing anything else.', 'cat': 'Secret', 'secret': True},
    'mirror': {'name': 'Mirror 🪞', 'desc': 'Guess same word twice in a row (on cooldown).', 'cat': 'Secret', 'secret': True},
    'bot_guess': {'name': 'The Bot? 🤖', 'desc': 'Try to guess the bot name.', 'cat': 'Secret', 'secret': True},
    'wrong_place': {'name': 'Wrong Place 📍', 'desc': 'Use game command in wrong channel.', 'cat': 'Secret', 'secret': True},
    'spammer': {'name': 'Spammer 📢', 'desc': 'Guess 5 times while on cooldown.', 'cat': 'Secret', 'secret': True},
    'hello': {'name': 'Hello? 📞', 'desc': 'Use !nexthint when all hints are out.', 'cat': 'Secret', 'secret': True},
    'checking': {'name': 'Just checking 🔍', 'desc': 'Use !ach 5 times in a minute.', 'cat': 'Secret', 'secret': True},
    'wealthy': {'name': 'Wealthy 💰', 'desc': 'Check leaderboard while being #1.', 'cat': 'Secret', 'secret': True},
    'nice_try': {'name': 'Nice Try 🤡', 'desc': 'Guess "correct" or "answer".', 'cat': 'Secret', 'secret': True},
    'quick_math': {'name': 'Quick Math 🔢', 'desc': 'Try to guess a number.', 'cat': 'Secret', 'secret': True},
    'tired': {'name': 'Tired 💤', 'desc': 'Guess "idk" or "i don\'t know".', 'cat': 'Secret', 'secret': True},
    'rebel': {'name': 'Rebel ⚔️', 'desc': 'Use admin command without permission.', 'cat': 'Secret', 'secret': True},
    'luck_irish': {'name': 'Luck of the Irish 🌈', 'desc': 'Use !pray before winning.', 'cat': 'Secret', 'secret': True},
    'deep_sleeper': {'name': 'Deep Sleeper 😴', 'desc': 'Guess between 3:00 AM and 4:00 AM.', 'cat': 'Secret', 'secret': True},
    'socialite': {'name': 'Socialite 💬', 'desc': 'Use !wins 10 times.', 'cat': 'Secret', 'secret': True},
    'completionist': {'name': 'Completionist 🏆', 'desc': 'Unlock 49 other achievements.', 'cat': 'Secret', 'secret': True},
}

# --- DATABASE SETUP ---
mongo_uri = os.getenv('MONGO_URI')
if not mongo_uri:
    print("❌ ERROR: MONGO_URI missing! Persistence disabled.")
    sys.exit(1)

mongo_client = AsyncIOMotorClient(mongo_uri)
db = mongo_client[CONFIG['DB_NAME']]

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- Runtime Variables ---
is_game_active = False
current_game_id = None
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
hint_timing_minutes = CONFIG['DEFAULT_HINT_TIMING_MINUTES']
last_hint_reveal_time = None
game_start_time = None
game_queue = {} # Slouží jako cache pro DB

# Session tracking
session_guesses = {} # user_id: [guesses]
last_winner_time = None
last_winner_id = None
recent_commands = {} # user_id: [(cmd, time)]
last_global_guess = {'text': '', 'time': None} # Pro Copycat achievement

# --- UTILS ---

def format_time_remaining(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "a moment"

def get_ping_role_string(role_key):
    roles = CONFIG.get(role_key, [])
    if isinstance(roles, int): return f"<@&{roles}>"
    return "".join([f"<@&{rid}> " for rid in roles])

async def load_game_state_from_db():
    global is_game_active, current_game_id, correct_answer, current_hints_storage, current_hints_revealed, last_hint_reveal_time, game_queue, hint_timing_minutes, game_start_time, last_winner_id, last_winner_time
    
    doc = await db.game_state.find_one({'_id': 'main_state'})
    if not doc:
        game_queue = {str(i): {'item': None, 'hints': {}} for i in range(1, 6)}
        await db.game_state.insert_one({'_id': 'main_state', 'queue': game_queue})
        return

    is_game_active = doc.get('is_active', False)
    current_game_id = doc.get('current_id')
    correct_answer = doc.get('answer')
    raw_hints = doc.get('hints_storage', {})
    current_hints_storage = {int(k): v for k, v in raw_hints.items()}
    current_hints_revealed = doc.get('revealed', [])
    hint_timing_minutes = doc.get('timing', 60)
    game_queue = doc.get('queue', {})
    last_winner_id = doc.get('last_winner_id')
    
    if doc.get('last_reveal'): last_hint_reveal_time = doc['last_reveal']
    if doc.get('start_time'): game_start_time = doc['start_time']
    if doc.get('last_winner_time'): last_winner_time = doc['last_winner_time']

async def save_game_state_to_db():
    state = {
        'is_active': is_game_active,
        'current_id': current_game_id,
        'answer': correct_answer,
        'hints_storage': {str(k): v for k, v in current_hints_storage.items()},
        'revealed': current_hints_revealed,
        'timing': hint_timing_minutes,
        'queue': game_queue,
        'last_reveal': last_hint_reveal_time,
        'start_time': game_start_time,
        'last_winner_id': last_winner_id,
        'last_winner_time': last_winner_time
    }
    await db.game_state.update_one({'_id': 'main_state'}, {'$set': state}, upsert=True)

# --- ACHIEVEMENT ENGINE ---

async def grant_achievement(user: discord.Member, ach_id: str):
    if ach_id not in ACHIEVEMENTS: return
    
    user_doc = await db.users.find_one({'_id': user.id})
    if user_doc and ach_id in user_doc.get('achievements', []):
        return

    await db.users.update_one(
        {'_id': user.id},
        {'$addToSet': {'achievements': ach_id}},
        upsert=True
    )

    ch = bot.get_channel(CONFIG['ACHIEVEMENT_CHANNEL_ID'])
    ach_data = ACHIEVEMENTS[ach_id]
    if ch:
        embed = discord.Embed(
            title="🏆 Achievement Unlocked!",
            description=f"{user.mention} Got achievement: **{ach_data['name']}**",
            color=discord.Color.gold()
        )
        embed.set_footer(text=ach_data['desc'])
        await ch.send(content=user.mention, embed=embed)
    
    # Check Completionist & Halfway
    if ach_id != 'completionist':
        updated_doc = await db.users.find_one({'_id': user.id})
        count = len(updated_doc.get('achievements', []))
        if count >= 49 and 'completionist' not in updated_doc.get('achievements', []):
             await grant_achievement(user, 'completionist')
        if count >= 25 and 'halfway' not in updated_doc.get('achievements', []):
             await grant_achievement(user, 'halfway')

async def update_stat(user_id, stat_key, increment=1, set_val=None):
    update = {'$inc': {f'stats.{stat_key}': increment}}
    if set_val is not None:
        update = {'$set': {f'stats.{stat_key}': set_val}}
        
    await db.users.update_one({'_id': user_id}, update, upsert=True)

async def check_command_spam_achievements(ctx, cmd_name):
    uid = ctx.author.id
    now = datetime.now()
    if uid not in recent_commands: recent_commands[uid] = []
    recent_commands[uid] = [x for x in recent_commands[uid] if (now - x[1]).total_seconds() < 60]
    recent_commands[uid].append((cmd_name, now))
    cmds = recent_commands[uid]
    
    if cmd_name == 'mywins':
        mywins = [t for c, t in cmds if c == 'mywins']
        if len(mywins) >= 3 and (mywins[-1] - mywins[-3]).total_seconds() <= 10:
             await grant_achievement(ctx.author, 'self_check')

    if cmd_name == 'ach' and len([c for c, t in cmds if c == 'ach']) >= 5:
         await grant_achievement(ctx.author, 'checking')
            
    if cmd_name == 'wins':
        await update_stat(uid, 'socialite_count')
        u = await db.users.find_one({'_id': uid})
        if u and u.get('stats', {}).get('socialite_count', 0) >= 10:
            await grant_achievement(ctx.author, 'socialite')

# --- TASKS ---
@tasks.loop(minutes=1)
async def hint_timer():
    global current_hints_revealed, last_hint_reveal_time
    if not is_game_active or not last_hint_reveal_time: return
    
    now = datetime.now()
    next_reveal = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
    
    if now >= next_reveal:
        nxt = len(current_hints_revealed) + 1
        if nxt in current_hints_storage:
            ch = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
            if ch:
                txt = current_hints_storage[nxt]
                ping = get_ping_role_string('HINT_PING_ROLE_IDS')
                await ch.send(f"{ping}Hint {nxt}: {txt}")
                current_hints_revealed.append({'hint_number': nxt, 'text': txt})
                last_hint_reveal_time = now
                await save_game_state_to_db()
        else:
            hint_timer.stop()

@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user}')
    await db.users.create_index([("wins", -1)]) # Speed up leaderboard
    await load_game_state_from_db()
    if is_game_active:
        if not hint_timer.is_running(): hint_timer.start()
        await bot.change_presence(activity=discord.Game(name="Guess the item! (!guess)"))
    else:
        await bot.change_presence(activity=discord.Game(name="Waiting for setup"))

@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

# --- ADMIN COMMANDS ---

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def start(ctx, game_id: int):
    global is_game_active, current_game_id, correct_answer, current_hints_storage, current_hints_revealed, last_hint_reveal_time, game_start_time, session_guesses
    
    if is_game_active: return await ctx.send("❌ Game active. Use `!stop` first.")
    s_id = str(game_id)
    if s_id not in game_queue or not game_queue[s_id]['item']: return await ctx.send("❌ Slot empty.")

    data = game_queue[s_id]
    correct_answer = data['item']
    current_hints_storage = {int(k): v for k,v in data['hints'].items()}
    
    if len(current_hints_storage) != CONFIG['REQUIRED_HINTS']:
        return await ctx.send("❌ Missing hints.")

    is_game_active = True
    current_game_id = game_id
    current_hints_revealed = []
    session_guesses = {} # Reset session tracking
    # Reset specific stats for new game if needed
    
    game_start_time = datetime.now()
    last_hint_reveal_time = game_start_time
    
    if not hint_timer.is_running(): hint_timer.start()
    
    ch = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
    if ch:
        h1 = current_hints_storage[1]
        current_hints_revealed.append({'hint_number': 1, 'text': h1})
        ping = get_ping_role_string('HINT_PING_ROLE_IDS')
        await ch.send(f"{ping}New game has started. Good luck ☘️\n\n**Hint 1:** {h1}")
        await ctx.send("✅ Started.")
        await save_game_state_to_db()

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def stop(ctx, game_id: int = None):
    global is_game_active, correct_answer, current_game_id, game_queue
    
    if game_id is None: # Force stop current
        if is_game_active:
            is_game_active = False
            correct_answer = None
            if hint_timer.is_running(): hint_timer.stop()
            await ctx.send(f"🛑 Stopped active game.")
            await save_game_state_to_db()
        else:
            await ctx.send("No active game.")
    else: # Clear slot
        s_id = str(game_id)
        if is_game_active and current_game_id == game_id:
            is_game_active = False
            if hint_timer.is_running(): hint_timer.stop()
        
        game_queue[s_id] = {'item': None, 'hints': {}}
        await ctx.send(f"🗑️ Cleared slot #{game_id}")
        await save_game_state_to_db()

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def stopall(ctx):
    global is_game_active, game_queue
    is_game_active = False
    if hint_timer.is_running(): hint_timer.stop()
    game_queue = {str(i): {'item': None, 'hints': {}} for i in range(1, 6)}
    await save_game_state_to_db()
    await ctx.send("🚨 All stopped and cleared.")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def setitem(ctx, gid: int, *, item: str):
    if 1<=gid<=5:
        game_queue[str(gid)]['item'] = item.strip()
        await save_game_state_to_db()
        await ctx.send(f"✅ Slot {gid} item: {item}")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def sethint(ctx, gid: int, num: int, *, text: str):
    if 1<=gid<=5 and 1<=num<=7:
        if 'hints' not in game_queue[str(gid)]: game_queue[str(gid)]['hints'] = {}
        game_queue[str(gid)]['hints'][str(num)] = text.strip()
        await save_game_state_to_db()
        await ctx.send(f"✅ Slot {gid} Hint {num} set.")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def setallhints(ctx, gid: int, *, text: str):
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) == 7:
        game_queue[str(gid)]['hints'] = {str(i+1): l for i,l in enumerate(lines)}
        await save_game_state_to_db()
        await ctx.send(f"✅ Slot {gid} hints set.")
    else:
        await ctx.send("❌ Need 7 lines.")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def sethinttiming(ctx, minutes: int):
    global hint_timing_minutes
    if 1 <= minutes <= 60:
        hint_timing_minutes = minutes
        await save_game_state_to_db()
        await ctx.send(f"✅ Timing set to {minutes}m.")
    else:
        await ctx.send("❌ 1-60 mins only.")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def revealhint(ctx):
    global current_hints_revealed, last_hint_reveal_time
    if not is_game_active: return await ctx.send("No game active.")
    
    nxt = len(current_hints_revealed) + 1
    if nxt <= CONFIG['REQUIRED_HINTS']:
         txt = current_hints_storage[nxt]
         ch = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
         if ch:
             await ch.send(f"{get_ping_role_string('HINT_PING_ROLE_IDS')}Manual Hint {nxt}: {txt}")
         current_hints_revealed.append({'hint_number': nxt, 'text': txt})
         last_hint_reveal_time = datetime.now()
         await save_game_state_to_db()
         await ctx.send(f"✅ Hint {nxt} revealed.")
    else:
        await ctx.send("All revealed.")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def queue(ctx):
    embed = discord.Embed(title="Game Queue", color=discord.Color.gold())
    for i in range(1, 6):
        sid = str(i)
        data = game_queue.get(sid, {})
        item = data.get('item', '❌ Empty')
        h_count = len(data.get('hints', {}))
        status = "Waiting"
        if is_game_active and current_game_id == i: status = "**ACTIVE**"
        embed.add_field(name=f"Slot #{i} ({status})", value=f"Item: {item}\nHints: {h_count}/7", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def status(ctx):
    if is_game_active:
        await ctx.send(f"🟢 Active Game #{current_game_id}. Hints: {len(current_hints_revealed)}/7. Next in: {hint_timing_minutes}m.")
    else:
        await ctx.send("🔴 No game active.")

@bot.command()
@commands.has_any_role(*CONFIG['ADMIN_ROLE_IDS'])
async def testping(ctx):
    await ctx.send(f"Ping: {get_ping_role_string('HINT_PING_ROLE_IDS')}")

# --- PLAYER COMMANDS ---

@bot.command()
async def pray(ctx):
    await check_command_spam_achievements(ctx, 'pray')
    await grant_achievement(ctx.author, 'good_luck')
    await update_stat(ctx.author.id, 'last_pray_time', set_val=datetime.now().timestamp())
    await ctx.send("Bruh <:emoji:1397930098916851773>")

@bot.command()
async def guess(ctx, *, guess: str):
    global is_game_active, last_winner_time, last_winner_id, correct_answer, last_global_guess
    
    if ctx.channel.id == CONFIG['WINS_CHANNEL_ID']: return
    
    uid = ctx.author.id
    now = datetime.now()
    g_low = guess.lower().strip()

    # 11. Too Late Logic (Must come before active check)
    if not is_game_active:
        if last_winner_time and (now - last_winner_time).total_seconds() <= 7.5:
            await grant_achievement(ctx.author, 'too_late')
        await grant_achievement(ctx.author, 'ghost')
        await ctx.send("No active game.")
        return

    # Check cooldown
    user_doc = await db.users.find_one({'_id': uid})
    last_guess = None
    if user_doc and 'last_guess_ts' in user_doc:
         last_guess = datetime.fromtimestamp(user_doc['last_guess_ts'])

    if last_guess:
        diff = now - last_guess
        if diff < timedelta(minutes=CONFIG['GUESS_COOLDOWN_MINUTES']):
            rem = int((timedelta(minutes=CONFIG['GUESS_COOLDOWN_MINUTES']) - diff).total_seconds())
            await update_stat(uid, 'cooldown_hits')
            
            # Re-fetch for stats
            u_fresh = await db.users.find_one({'_id': uid})
            cd_hits = u_fresh.get('stats', {}).get('cooldown_hits', 0)
            
            if cd_hits >= 3: await grant_achievement(ctx.author, 'speed_limit')
            if cd_hits >= 5: await grant_achievement(ctx.author, 'spammer')
            
            if last_global_guess['text'] == g_low: await grant_achievement(ctx.author, 'mirror')
                
            await ctx.reply(f"🛑 Cooldown! Wait **{format_time_remaining(rem)}**.", delete_after=5)
            return

    # Valid guess processing
    await db.users.update_one({'_id': uid}, {'$set': {'last_guess_ts': now.timestamp()}, '$inc': {'guesses': 1}}, upsert=True)
    await update_stat(uid, 'total_guesses')
    
    if uid not in session_guesses: session_guesses[uid] = []
    session_guesses[uid].append({'text': guess, 'time': now})

    # Global Copycat check
    if last_global_guess['time'] and (now - last_global_guess['time']).total_seconds() <= 5:
        if last_global_guess['text'] == g_low:
             await grant_achievement(ctx.author, 'copycat')
    
    last_global_guess = {'text': g_low, 'time': now}

    # --- ACHIEVEMENTS CHECK ---
    u = await db.users.find_one({'_id': uid})
    total = u.get('guesses', 0)
    
    if total >= 50: await grant_achievement(ctx.author, 'persistent')
    if total >= 100: await grant_achievement(ctx.author, 'addict')
    if g_low in ['life', 'everything', 'word']: await grant_achievement(ctx.author, 'philosopher')
    if g_low in ['idk', "i don't know"]: await grant_achievement(ctx.author, 'tired')
    if g_low.isdigit(): await grant_achievement(ctx.author, 'quick_math')
    if g_low in ['correct', 'answer']: await grant_achievement(ctx.author, 'nice_try')
    if "bot" in g_low: await grant_achievement(ctx.author, 'bot_guess')
    if len(guess) > 15: await grant_achievement(ctx.author, 'keyboard')
    
    same_guesses = [x['text'] for x in session_guesses[uid] if x['text'].lower() == g_low]
    if len(same_guesses) >= 5: await grant_achievement(ctx.author, 'again')
    if len(session_guesses[uid]) >= 20: await grant_achievement(ctx.author, 'workaholic')
    
    # Unique guesses for Brute Force
    unique_guesses = set(x['text'].lower() for x in session_guesses[uid])
    if len(unique_guesses) >= 10: await grant_achievement(ctx.author, 'brute_force')

    prev_ans = (await db.game_state.find_one({'_id': 'main_state'})).get('last_correct_answer')
    if prev_ans and g_low == prev_ans.lower(): await grant_achievement(ctx.author, 'maybe')

    # --- CHECK CORRECT ---
    if g_low == correct_answer.lower():
        await ctx.message.add_reaction('✅')
        
        last_pray = u.get('stats', {}).get('last_pray_time', 0)
        if (now.timestamp() - last_pray) < 60: await grant_achievement(ctx.author, 'luck_irish')
        
        if len(session_guesses[uid]) == 1: await grant_achievement(ctx.author, 'silent')

        elapsed = (now - game_start_time).total_seconds()
        if len(current_hints_revealed) == 1:
            await grant_achievement(ctx.author, 'sniper')
            if elapsed < 60: await grant_achievement(ctx.author, 'speedrunner')
            
        if elapsed < 120: await grant_achievement(ctx.author, 'close_call')
        if len(current_hints_revealed) == 7: await grant_achievement(ctx.author, 'finisher')

        wrong_count = len([x for x in session_guesses[uid] if x['text'].lower() != g_low])
        if wrong_count >= 5: await grant_achievement(ctx.author, 'getting_there')
        if (now - last_hint_reveal_time).total_seconds() <= 10: await grant_achievement(ctx.author, 'sharp_eye')

        await db.users.update_one({'_id': uid}, {'$inc': {'wins': 1}})
        new_wins = u.get('wins', 0) + 1
        
        if new_wins == 1: await grant_achievement(ctx.author, 'first_blood')
        if new_wins == 25: await grant_achievement(ctx.author, 'veteran')
        if new_wins == 100: await grant_achievement(ctx.author, 'legend')
        
        if last_winner_id == uid: await grant_achievement(ctx.author, 'double_down')

        # Roles
        try:
            role_cfg = CONFIG['WINNER_ROLES_CONFIG']
            target_rid = None
            for w, rid in sorted(role_cfg.items(), reverse=True):
                if new_wins >= w:
                    target_rid = rid
                    break
            if target_rid:
                role = ctx.guild.get_role(target_rid)
                if role and role not in ctx.author.roles:
                    await ctx.author.add_roles(role)
        except: pass

        ach = bot.get_channel(CONFIG['WINNER_ANNOUNCEMENT_CHANNEL_ID'])
        if ach: await ach.send(f"🏆 **WINNER!** {ctx.author.mention} guessed the item: **{correct_answer}**!")
        
        await ctx.send(f"{get_ping_role_string('GAME_END_PING_ROLE_ID')} ✅ Game Over! Item found.")
        
        # Save state
        finished_id = current_game_id
        last_winner_time = now
        last_winner_id = uid
        await db.game_state.update_one({'_id': 'main_state'}, {'$set': {'last_correct_answer': correct_answer}})

        is_game_active = False
        if hint_timer.is_running(): hint_timer.stop()
        await save_game_state_to_db()

        # --- STEALTH AUTO START ---
        # No big announcements, just wait and start
        next_id = finished_id + 1
        if str(next_id) in game_queue and game_queue[str(next_id)]['item']:
            await asyncio.sleep(15) # Short silence
            if str(next_id) in game_queue and game_queue[str(next_id)]['item']:
                await start(ctx, next_id)
        else:
             await ctx.send("🏁 Queue Finished.")

    else:
        await ctx.send(f"❌ Wrong. Retry in 30m.")

@bot.command()
async def ach(ctx):
    await check_command_spam_achievements(ctx, 'ach')
    user_doc = await db.users.find_one({'_id': ctx.author.id})
    unlocked = user_doc.get('achievements', []) if user_doc else []
    
    embed = discord.Embed(title=f"Achievements - {ctx.author.display_name}", color=discord.Color.purple())
    categories = {'General': [], 'Skill': [], 'Grind': [], 'Secret': []}
    
    for aid, data in ACHIEVEMENTS.items():
        cat = data.get('cat', 'General')
        is_done = aid in unlocked
        icon = "✅" if is_done else "🔒"
        desc = data['desc']
        if data.get('secret', False) and not is_done: desc = "???"
        categories[cat].append(f"{icon} **{data['name']}**\n_{desc}_")
    
    for cat, lines in categories.items():
        if lines: embed.add_field(name=f"--- {cat} ---", value="\n".join(lines), inline=False)
            
    progress = f"{len(unlocked)}/50"
    embed.set_footer(text=f"Progress: {progress}")
    await ctx.send(embed=embed)

@bot.command(aliases=['lbc', 'top'])
async def wins(ctx):
    await check_command_spam_achievements(ctx, 'wins')
    cursor = db.users.find().sort('wins', -1).limit(10)
    users = await cursor.to_list(length=10)
    
    if users and users[0]['_id'] == ctx.author.id:
        await grant_achievement(ctx.author, 'wealthy')
        
    desc = "\n".join([f"**{i+1}.** <@{u['_id']}> - {u.get('wins',0)}" for i, u in enumerate(users)])
    await ctx.send(embed=discord.Embed(title="Top 10 Wins", description=desc, color=discord.Color.gold()))

@bot.command()
async def mywins(ctx):
    await check_command_spam_achievements(ctx, 'mywins')
    u = await db.users.find_one({'_id': ctx.author.id})
    c = u.get('wins', 0) if u else 0
    await ctx.send(f"You have {c} wins.")

@bot.command()
async def current(ctx):
    if not is_game_active: return await ctx.send("No active game.")
    embed = discord.Embed(title=f"Hints ({len(current_hints_revealed)}/{CONFIG['REQUIRED_HINTS']})", color=discord.Color.teal())
    for h in current_hints_revealed: embed.add_field(name=f"#{h['hint_number']}", value=h['text'], inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def nexthint(ctx):
    await check_command_spam_achievements(ctx, 'nexthint')
    if len(current_hints_revealed) >= CONFIG['REQUIRED_HINTS']:
        await grant_achievement(ctx.author, 'hello')
        return await ctx.send("All hints revealed.")
        
    await update_stat(ctx.author.id, 'nexthint_spam')
    if is_game_active and last_hint_reveal_time:
        nxt = last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)
        rem = int((nxt - datetime.now()).total_seconds())
        await ctx.send(f"⏳ Next hint: {format_time_remaining(rem)}")

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📚 Command List", color=discord.Color.blue())
    
    player_cmds = """
    `!guess <item>` - Attempt to guess
    `!lbc`, `!top` - Leaderboard
    `!mywins` - Your stats
    `!ach` - Achievements
    `!current` - Show all hints
    `!nexthint` - Time to next hint
    `!pray` - 🙏
    """
    
    staff_cmds = """
    `!setitem <game-number> <name>`
    `!sethint <game-number> <#> <text>`
    `!setallhints <game-number> <text>`
    `!start <game-number>`
    `!stop`, `!stop <game-number>`
    `!stopall`
    `!revealhint`
    `!queue`
    `!status`
    """
    
    embed.add_field(name="🎮 Player", value=player_cmds, inline=False)
    embed.add_field(name="🛠️ Staff", value=staff_cmds, inline=False)
    await ctx.send(embed=embed)

# --- STARTUP ---
def run_flask():
    app.run(host='0.0.0.0', port=int(WEB_PORT))

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN: print("Error: DISCORD_TOKEN missing.")
    else: bot.run(TOKEN)
