import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
from datetime import datetime, timedelta
import threading
import asyncio
import sys
from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
import certifi
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
    
    'DB_NAME': 'discord_game_bot',

    # KANÁLY
    'WINS_CHANNEL_ID': 1441693640089927680, 
    'WINNER_ANNOUNCEMENT_CHANNEL_ID': 1441858034291708059, 
    'HINT_CHANNEL_ID': 1441386236844572834, 
    'ACHIEVEMENT_CHANNEL_ID': 1457293868876955785,
    'STAFF_HELP_CHANNEL_ID': 1441894319744352388,

    # PINGS
    'HINT_PING_ROLE_IDS': [1441388270201077882],
    'GAME_END_PING_ROLE_ID': 1441386642332979200,

    'WINNER_ROLES_CONFIG': {
        1: 1441693698776764486, 5: 1441693984266129469, 10: 1441694043477381150,
        25: 1441694109268967505, 50: 1441694179011989534, 100: 1441694438345674855
    }
}

# --- DEFINICE ACHIEVEMENTŮ ---
ACHIEVEMENTS = {
    'first_blood': {'name': 'Feels good right? 🩸', 'desc': 'Win your first game.', 'cat': 'General'},
    'persistent': {'name': 'Persistent 🔨', 'desc': 'Submit a total of 50 guesses.', 'cat': 'General', 'target': 50},
    'good_luck': {'name': 'Good Luck 🍀', 'desc': 'Use the /pray command.', 'cat': 'General'},
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
    'ghost': {'name': 'Ghost Guesser 👻', 'desc': 'Use /guess when no game is active.', 'cat': 'Secret', 'secret': True},
    'speed_limit': {'name': 'Speed Limit 🛑', 'desc': 'Try to guess 3 times while on cooldown.', 'cat': 'Secret', 'secret': True},
    'copycat': {'name': 'Copycat 🐈', 'desc': 'Guess the same thing as someone else within 5s.', 'cat': 'Secret', 'secret': True},
    'maybe': {'name': 'Maybe? ❓', 'desc': 'Guess the answer from the previous game.', 'cat': 'Secret', 'secret': True},
    'philosopher': {'name': 'The Philosopher 🌍', 'desc': 'Guess "life", "everything", or "word".', 'cat': 'Secret', 'secret': True},
    'keyboard': {'name': 'Keyboard Warrior ⌨️', 'desc': 'Send a guess longer than 50 chars.', 'cat': 'Secret', 'secret': True},
    'self_check': {'name': 'Self-Check 🧐', 'desc': 'Use /mywins 3 times in 10s.', 'cat': 'Secret', 'secret': True},
    'impatience': {'name': 'Impatience 💢', 'desc': 'Use /nexthint 5 times in one game.', 'cat': 'Secret', 'secret': True},
    'silent': {'name': 'Silent Winner 🤫', 'desc': 'Win without typing anything else.', 'cat': 'Secret', 'secret': True},
    'mirror': {'name': 'Mirror 🪞', 'desc': 'Guess same word twice in a row (on cooldown).', 'cat': 'Secret', 'secret': True},
    'bot_guess': {'name': 'The Bot? 🤖', 'desc': 'Try to guess the bot name.', 'cat': 'Secret', 'secret': True},
    'wrong_place': {'name': 'Wrong Place 📍', 'desc': 'Use game command in wrong channel.', 'cat': 'Secret', 'secret': True},
    'spammer': {'name': 'Spammer 📢', 'desc': 'Guess 5 times while on cooldown.', 'cat': 'Secret', 'secret': True},
    'hello': {'name': 'Hello? 📞', 'desc': 'Use /nexthint when all hints are out.', 'cat': 'Secret', 'secret': True},
    'checking': {'name': 'Just checking 🔍', 'desc': 'Use /ach 5 times in a minute.', 'cat': 'Secret', 'secret': True},
    'wealthy': {'name': 'Wealthy 💰', 'desc': 'Check leaderboard while being #1.', 'cat': 'Secret', 'secret': True},
    'nice_try': {'name': 'Nice Try 🤡', 'desc': 'Guess "correct" or "answer".', 'cat': 'Secret', 'secret': True},
    'quick_math': {'name': 'Quick Math 🔢', 'desc': 'Try to guess a number.', 'cat': 'Secret', 'secret': True},
    'tired': {'name': 'Tired 💤', 'desc': 'Guess "idk" or "i don\'t know".', 'cat': 'Secret', 'secret': True},
    'rebel': {'name': 'Rebel ⚔️', 'desc': 'Use admin command without permission.', 'cat': 'Secret', 'secret': True},
    'luck_irish': {'name': 'Luck of the Irish 🌈', 'desc': 'Use /pray before winning.', 'cat': 'Secret', 'secret': True},
    'deep_sleeper': {'name': 'Deep Sleeper 😴', 'desc': 'Guess between 3:00 AM and 4:00 AM.', 'cat': 'Secret', 'secret': True},
    'socialite': {'name': 'Socialite 💬', 'desc': 'Use leaderboard check 10 times.', 'cat': 'Secret', 'secret': True},
    'completionist': {'name': 'Completionist 🏆', 'desc': 'Unlock 49 other achievements.', 'cat': 'Secret', 'secret': True},
}

# --- DATABASE SETUP ---
mongo_uri = os.getenv('MONGO_URI')
if not mongo_uri:
    print("❌ ERROR: MONGO_URI missing!")
    sys.exit(1)

mongo_client = AsyncIOMotorClient(mongo_uri, tlsCAFile=certifi.where())
db = mongo_client[CONFIG['DB_NAME']]

# --- BOT SETUP ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True 
        super().__init__(command_prefix='!', intents=intents, help_command=None)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash commands synced.")

bot = MyBot()

# --- Runtime Variables ---
is_game_active = False
current_game_id = None
correct_answer = None
current_hints_storage = {}
current_hints_revealed = []
hint_timing_minutes = CONFIG['DEFAULT_HINT_TIMING_MINUTES']
last_hint_reveal_time = None
game_start_time = None
game_queue = {}
session_guesses = {}
last_winner_time = None
last_winner_id = None
last_global_guess = {'text': '', 'time': None}
cached_role_ids = {'admin': None, 'host': None}

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

async def update_leaderboard_channel(guild):
    lb_ch = guild.get_channel(CONFIG['WINS_CHANNEL_ID'])
    if not lb_ch: return
    try: await lb_ch.purge(limit=10)
    except: pass
    cursor = db.users.find().sort('wins', -1).limit(10)
    users = await cursor.to_list(length=10)
    desc = ""
    for i, u in enumerate(users):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"**{i+1}.**"
        desc += f"{medal} <@{u['_id']}> — **{u.get('wins', 0)} Wins**\n"
    embed = discord.Embed(title="🏆 TOP 10 PLAYERS", description=desc, color=discord.Color.gold())
    await lb_ch.send(embed=embed)

async def load_roles_config():
    doc = await db.config.find_one({'_id': 'roles'})
    if doc:
        cached_role_ids['admin'] = doc.get('admin_role')
        cached_role_ids['host'] = doc.get('host_role')

async def check_permissions(user: discord.Member):
    if not cached_role_ids['admin']: await load_roles_config()
    is_admin = bool(user.guild_permissions.administrator)
    if cached_role_ids['admin'] and user.guild.get_role(cached_role_ids['admin']) in user.roles: is_admin = True
    is_host = is_admin
    if cached_role_ids['host'] and user.guild.get_role(cached_role_ids['host']) in user.roles: is_host = True
    return {'player': True, 'host': is_host, 'admin': is_admin}

async def save_game_state_to_db():
    state = {
        'is_active': is_game_active, 'current_id': current_game_id, 'answer': correct_answer,
        'hints_storage': {str(k): v for k, v in current_hints_storage.items()},
        'revealed': current_hints_revealed, 'timing': hint_timing_minutes, 'queue': game_queue,
        'last_reveal': last_hint_reveal_time, 'start_time': game_start_time,
        'last_winner_id': last_winner_id, 'last_winner_time': last_winner_time
    }
    await db.game_state.update_one({'_id': 'main_state'}, {'$set': state}, upsert=True)

async def load_game_state_from_db():
    global is_game_active, current_game_id, correct_answer, current_hints_storage, current_hints_revealed, last_hint_reveal_time, game_queue, hint_timing_minutes, game_start_time, last_winner_id, last_winner_time
    await load_roles_config()
    doc = await db.game_state.find_one({'_id': 'main_state'})
    if not doc:
        game_queue = {str(i): {'item': None, 'hints': {}} for i in range(1, 6)}
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
    last_hint_reveal_time = doc.get('last_reveal')
    game_start_time = doc.get('start_time')
    last_winner_time = doc.get('last_winner_time')

async def grant_achievement(user: discord.Member, ach_id: str):
    if ach_id not in ACHIEVEMENTS: return
    user_doc = await db.users.find_one({'_id': user.id})
    if user_doc and ach_id in user_doc.get('achievements', []): return
    await db.users.update_one({'_id': user.id}, {'$addToSet': {'achievements': ach_id}}, upsert=True)
    ch = bot.get_channel(CONFIG['ACHIEVEMENT_CHANNEL_ID'])
    ach_data = ACHIEVEMENTS[ach_id]
    if ch:
        embed = discord.Embed(title="🏆 Achievement Unlocked!", description=f"{user.mention} Got: **{ach_data['name']}**\n{ach_data['desc']}", color=discord.Color.gold())
        await ch.send(embed=embed)

async def update_stat(user_id, stat_key, increment=1, set_val=None):
    update = {'$inc': {f'stats.{stat_key}': increment}}
    if set_val is not None: update = {'$set': {f'stats.{stat_key}': set_val}}
    await db.users.update_one({'_id': user_id}, update, upsert=True)

# --- VIEWS ---
class AchievementView(discord.ui.View):
    def __init__(self, user_achievements, user):
        super().__init__(timeout=180)
        self.user_achievements = user_achievements
        self.user = user

    async def update_message(self, interaction, category):
        embed = discord.Embed(title=f"🏆 Achievements • {category}", color=discord.Color.purple())
        lines = []
        for aid, data in ACHIEVEMENTS.items():
            if data.get('cat') != category: continue
            is_done = aid in self.user_achievements
            icon = "✅" if is_done else "🔒"
            name = data['name'] if not data.get('secret') or is_done else "???"
            desc = data['desc'] if not data.get('secret') or is_done else "???"
            lines.append(f"{icon} **{name}**\n_{desc}_")
        embed.description = "\n\n".join(lines)[:4000]
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="General", style=discord.ButtonStyle.primary)
    async def general_btn(self, interaction, button): await self.update_message(interaction, "General")
    @discord.ui.button(label="Skill", style=discord.ButtonStyle.success)
    async def skill_btn(self, interaction, button): await self.update_message(interaction, "Skill")
    @discord.ui.button(label="Grind", style=discord.ButtonStyle.danger)
    async def grind_btn(self, interaction, button): await self.update_message(interaction, "Grind")
    @discord.ui.button(label="Secret", style=discord.ButtonStyle.secondary)
    async def secret_btn(self, interaction, button): await self.update_message(interaction, "Secret")

# --- TASKS ---
@tasks.loop(minutes=1)
async def hint_timer():
    global current_hints_revealed, last_hint_reveal_time
    if not is_game_active or not last_hint_reveal_time: return
    now = datetime.now()
    if now >= last_hint_reveal_time + timedelta(minutes=hint_timing_minutes):
        nxt = len(current_hints_revealed) + 1
        if nxt in current_hints_storage:
            ch = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
            if ch:
                txt = current_hints_storage[nxt]
                await ch.send(f"{get_ping_role_string('HINT_PING_ROLE_IDS')}Hint {nxt}: {txt}")
                current_hints_revealed.append({'hint_number': nxt, 'text': txt})
                last_hint_reveal_time = now
                await save_game_state_to_db()
        else: hint_timer.stop()

# --- COMMANDS ---
@bot.event
async def on_ready():
    print(f'Bot {bot.user} ready')
    await load_game_state_from_db()
    if is_game_active: hint_timer.start()

# --- REUSABLE HELP GENERATOR ---
def get_help_embeds(perms):
    embed = discord.Embed(title="📚 Command List", color=discord.Color.blue())
    p_cmds = "**`!guess`** - Guess item\n**`/mywins`** - Stats\n**`/current`** - Hints\n**`/nexthint`** - Time\n**`/ach`** - Achievements"
    embed.add_field(name="🎮 Player Commands", value=p_cmds, inline=False)
    
    if perms['host']:
        h_cmds = "**`!setitem 1-5`**\n**`!sethint 1-5`**\n**`!setallhints 1-5`**\n**`!sethinttiming`** <:closed:1455972421491228673>\n**`!stop 1-5`** <:closed:1455972421491228673>\n**`!stopall`** <:closed:1455972421491228673>\n**`!start`**\n**`!revealhint`**\n**`!queue`**"
        embed.add_field(name="🛠️ Host Commands", value=h_cmds, inline=False)
        
    if perms['admin']:
        a_cmds = "**`/achgive`** <:closed:1455972421491228673>\n**`/achremove`** <:closed:1455972421491228673>\n**`/reset`** <:closed:1455972421491228673>\n**`/removewin`** <:closed:1455972421491228673>\n**`/fullreset`** <:closed:1455972421491228673>\n**`/setrole`**"
        embed.add_field(name="<:wadmin_IDS:1403028581889605652> Admin Commands", value=a_cmds, inline=False)
        d_cmds = "**`!status`**\n**`!testping`** <:closed:1455972421491228673>\n**`!reactiontest`**"
        embed.add_field(name="🪲 Dev Commands", value=d_cmds, inline=False)
        
    embed.set_footer(text="* <:closed:1455972421491228673> - do not use unless necessary *")
    return embed

@bot.tree.command(name="help")
async def slash_help(interaction: discord.Interaction):
    perms = await check_permissions(interaction.user)
    await interaction.response.send_message(embed=get_help_embeds(perms), ephemeral=True)

@bot.command()
async def staffhelp(ctx):
    perms = await check_permissions(ctx.author)
    if not perms['admin']: return
    ch = bot.get_channel(CONFIG['STAFF_HELP_CHANNEL_ID'])
    if ch:
        # Full perms view for staff channel
        full_perms = {'host': True, 'admin': True}
        await ch.purge(limit=5)
        await ch.send(embed=get_help_embeds(full_perms))
        await ctx.send("✅ Staff Help updated in target channel.")

@bot.command(name="guess")
async def prefix_guess(ctx, *, guess: str):
    global is_game_active, last_winner_time, last_winner_id, correct_answer, last_global_guess
    uid, now, g_low = ctx.author.id, datetime.now(), guess.lower().strip()

    if not is_game_active:
        await ctx.message.add_reaction('❌')
        return

    user_doc = await db.users.find_one({'_id': uid})
    if user_doc and 'last_guess_ts' in user_doc:
        diff = now - datetime.fromtimestamp(user_doc['last_guess_ts'])
        if diff < timedelta(minutes=CONFIG['GUESS_COOLDOWN_MINUTES']):
            rem = int((timedelta(minutes=CONFIG['GUESS_COOLDOWN_MINUTES']) - diff).total_seconds())
            await ctx.send(f"{ctx.author.mention} 🛑 Cooldown! Wait **{format_time_remaining(rem)}**.", delete_after=3)
            await ctx.message.add_reaction('❌')
            return

    await db.users.update_one({'_id': uid}, {'$set': {'last_guess_ts': now.timestamp()}, '$inc': {'guesses': 1}}, upsert=True)
    last_global_guess = {'text': g_low, 'time': now}

    if g_low == correct_answer.lower():
        await ctx.message.add_reaction('✅')
        is_game_active = False
        hint_timer.stop()
        last_winner_time, last_winner_id = now, uid
        await db.users.update_one({'_id': uid}, {'$inc': {'wins': 1}})
        await save_game_state_to_db()
        await ctx.send(f"{get_ping_role_string('GAME_END_PING_ROLE_ID')} The round has ended. The item was **{correct_answer}**.")
        ann_ch = bot.get_channel(CONFIG['WINNER_ANNOUNCEMENT_CHANNEL_ID'])
        if ann_ch: await ann_ch.send(f"🏆 **WINNER!** {ctx.author.mention} guessed: **{correct_answer}**!")
        await update_leaderboard_channel(ctx.guild)
    else: await ctx.message.add_reaction('❌')

@bot.tree.command(name="ach")
async def slash_ach(interaction: discord.Interaction):
    user_doc = await db.users.find_one({'_id': interaction.user.id})
    unlocked = user_doc.get('achievements', []) if user_doc else []
    view = AchievementView(unlocked, interaction.user)
    await interaction.response.send_message(embed=discord.Embed(title="🏆 Achievements"), view=view, ephemeral=True)

@bot.tree.command(name="mywins")
async def slash_mywins(interaction: discord.Interaction):
    u = await db.users.find_one({'_id': interaction.user.id})
    await interaction.response.send_message(f"Wins: **{u.get('wins', 0) if u else 0}**")

@bot.tree.command(name="current")
async def slash_current(interaction: discord.Interaction):
    if not is_game_active: return await interaction.response.send_message("No game.", ephemeral=True)
    emb = discord.Embed(title="Revealed Hints")
    for h in current_hints_revealed: emb.add_field(name=f"#{h['hint_number']}", value=h['text'], inline=False)
    await interaction.response.send_message(embed=emb, ephemeral=True)

@bot.tree.command(name="nexthint")
async def slash_nexthint(interaction: discord.Interaction):
    if not is_game_active or not last_hint_reveal_time: return await interaction.response.send_message("Inactive.", ephemeral=True)
    rem = int(((last_hint_reveal_time + timedelta(minutes=hint_timing_minutes)) - datetime.now()).total_seconds())
    await interaction.response.send_message(f"⏳ Next: {format_time_remaining(rem)}", ephemeral=True)

# --- ADMIN SLASH ---
@bot.tree.command(name="achgive")
async def slash_achgive(interaction: discord.Interaction, member: discord.Member, achievement_id: str):
    if not (await check_permissions(interaction.user))['admin']: return
    await grant_achievement(member, achievement_id)
    await interaction.response.send_message(f"✅ Given {achievement_id}")

@bot.tree.command(name="achremove")
async def slash_achremove(interaction: discord.Interaction, member: discord.Member, achievement_id: str):
    if not (await check_permissions(interaction.user))['admin']: return
    await db.users.update_one({'_id': member.id}, {'$pull': {'achievements': achievement_id}})
    await interaction.response.send_message(f"✅ Removed {achievement_id}")

@bot.tree.command(name="reset")
async def slash_reset(interaction: discord.Interaction, member: discord.Member):
    if not (await check_permissions(interaction.user))['admin']: return
    await db.users.update_one({'_id': member.id}, {'$set': {'wins': 0, 'guesses': 0, 'stats': {}}})
    await interaction.response.send_message(f"✅ Reset {member}")

@bot.tree.command(name="removewin")
async def slash_removewin(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not (await check_permissions(interaction.user))['admin']: return
    await db.users.update_one({'_id': member.id}, {'$inc': {'wins': -amount}})
    await interaction.response.send_message(f"✅ Removed {amount} wins")

@bot.tree.command(name="fullreset")
async def slash_fullreset(interaction: discord.Interaction):
    if not (await check_permissions(interaction.user))['admin']: return
    await db.users.drop()
    await db.game_state.drop()
    await interaction.response.send_message("🔥 Wiped.")

@bot.tree.command(name="setrole")
@app_commands.choices(type=[app_commands.Choice(name="Admin", value="admin"), app_commands.Choice(name="Host", value="host")])
async def slash_setrole(interaction: discord.Interaction, type: app_commands.Choice[str], role: discord.Role):
    if not interaction.user.guild_permissions.administrator: return
    await db.config.update_one({'_id': 'roles'}, {'$set': {f"{type.value}_role": role.id}}, upsert=True)
    cached_role_ids[type.value] = role.id
    await interaction.response.send_message(f"✅ Set {type.value} role.")

# --- HOST PREFIX ---
@bot.command()
async def start(ctx, game_id: int):
    if not (await check_permissions(ctx.author))['host']: return
    global is_game_active, current_game_id, correct_answer, current_hints_storage, current_hints_revealed, last_hint_reveal_time, game_start_time
    s_id = str(game_id)
    if s_id not in game_queue or not game_queue[s_id]['item']: return await ctx.send("Empty.")
    data = game_queue[s_id]
    correct_answer = data['item']
    current_hints_storage = {int(k): v for k,v in data['hints'].items()}
    is_game_active, current_game_id = True, game_id
    current_hints_revealed = []
    game_start_time = last_hint_reveal_time = datetime.now()
    hint_timer.start()
    ch = bot.get_channel(CONFIG['HINT_CHANNEL_ID'])
    if ch:
        h1 = current_hints_storage[1]
        current_hints_revealed.append({'hint_number': 1, 'text': h1})
        await ch.send(f"{get_ping_role_string('HINT_PING_ROLE_IDS')}Game started!\n**Hint 1:** {h1}")
    await save_game_state_to_db()

@bot.command()
async def setitem(ctx, gid: int, *, item: str):
    if not (await check_permissions(ctx.author))['host']: return
    if 1<=gid<=5:
        if str(gid) not in game_queue: game_queue[str(gid)] = {'hints': {}}
        game_queue[str(gid)]['item'] = item.strip()
        await save_game_state_to_db()
        await ctx.send(f"✅ Slot {gid} item set.")

@bot.command()
async def sethint(ctx, gid: int, num: int, *, text: str):
    if not (await check_permissions(ctx.author))['host']: return
    game_queue[str(gid)]['hints'][str(num)] = text.strip()
    await save_game_state_to_db()
    await ctx.send(f"✅ Slot {gid} hint {num} set.")

@bot.command()
async def setallhints(ctx, gid: int, *, text: str):
    if not (await check_permissions(ctx.author))['host']: return
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) == 7:
        game_queue[str(gid)]['hints'] = {str(i+1): l for i,l in enumerate(lines)}
        await save_game_state_to_db()
        await ctx.send(f"✅ Slot {gid} all hints set.")

@bot.command()
async def queue(ctx):
    if not (await check_permissions(ctx.author))['host']: return
    emb = discord.Embed(title="Game Queue")
    for i in range(1, 6):
        d = game_queue.get(str(i), {})
        emb.add_field(name=f"Slot #{i}", value=f"Item: {d.get('item', '❌')}\nHints: {len(d.get('hints', {}))}/7", inline=False)
    await ctx.send(embed=emb)

# --- DEV ---
@bot.command()
async def reactiontest(ctx):
    if not (await check_permissions(ctx.author))['admin']: return
    await ctx.message.add_reaction('✅')
    await ctx.message.add_reaction('❌')

# --- RUN ---
def run_flask(): app.run(host='0.0.0.0', port=int(WEB_PORT))
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(os.getenv('DISCORD_TOKEN'))
