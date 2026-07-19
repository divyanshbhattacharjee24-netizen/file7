print("========== DIVYANSH TEST ==========")
import os
import discord
import random
import asyncpg
from discord.ext import commands, tasks
from datetime import timedelta, datetime, timezone
from collections import defaultdict, deque
from flask import Flask, jsonify
from threading import Thread

MODMAIL_CHANNEL_ID = 1393229169751752766
ADMIN_CHANNEL_ID = 1393229169751752766

# ---------------------------------------------------------------------------
# Automod configuration
# ---------------------------------------------------------------------------
# Roles (by name, case-insensitive) that are exempt from automod.
EXEMPT_ROLE_NAMES = {"admin", "administrator", "moderator", "mod", "staff"}

# Spam thresholds: (max_messages, within_seconds)
SPAM_THRESHOLDS = [
    (5, 5),   # 5 messages in 5 seconds
    (10, 10), # 10 messages in 10 seconds
]

# Identical-message repeat window (seconds)
DUPLICATE_WINDOW = 3

# Timeout duration applied to spammers (minutes)
AUTOMOD_TIMEOUT_MINUTES = 10

# How long (seconds) to suppress repeat punishments for the same user
PUNISHMENT_COOLDOWN = 60

# ---------------------------------------------------------------------------
# In-memory automod state
# ---------------------------------------------------------------------------
# { (user_id, channel_id): deque of UTC datetime objects }
_message_timestamps: dict[tuple[int, int], deque] = defaultdict(deque)

# { (user_id, channel_id): deque of (content, UTC datetime) tuples }
_message_contents: dict[tuple[int, int], deque] = defaultdict(deque)

# { user_id: UTC datetime } — when the user was last punished
_punishment_cooldowns: dict[int, datetime] = {}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

app = Flask(__name__)

@app.route("/stats")
def stats():
    return jsonify({
        "servers": 123
    })

def run_web():
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

# Tracks users currently going through the mod application flow.
# Key: user ID (int) → Value: dict with their collected answers and current step.
pending_applications: dict[int, dict] = {}

APPLICATION_QUESTIONS = [
    "Why do you want to be Mod in our server?",
    "How many hours will you be active on our server?",
    "What will you do if someone breaks a rule?",
    "What is your Discord username?",
]


class SubmitApplicationView(discord.ui.View):
    """A persistent View that shows a single Submit button for mod applications."""

    def __init__(self, user_id: int):
        # timeout=None keeps the button alive until clicked
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Submit", style=discord.ButtonStyle.success, emoji="📨")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only the applicant may click their own Submit button
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This button is not for you.", ephemeral=True
            )
            return

        app = pending_applications.pop(self.user_id, None)
        if app is None:
            await interaction.response.send_message(
                "⚠️ Could not find your application. Please run `!apply` again.",
                ephemeral=True,
            )
            return

        # Confirm to the applicant
        await interaction.response.send_message(
            "✅ Staff will reply soon! Your application has been submitted."
        )

        # Disable the button so it can't be clicked again
        button.disabled = True
        await interaction.message.edit(view=self)

        # Post the full application to the admin channel
        admin_channel = bot.get_channel(MODMAIL_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title="📋 New Mod Application",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Applicant",
                value=f"{interaction.user} (ID: {interaction.user.id})",
                inline=False,
            )
            for i, question in enumerate(APPLICATION_QUESTIONS):
                embed.add_field(
                    name=f"Q{i + 1}: {question}",
                    value=app["answers"][i] or "*(no answer)*",
                    inline=False,
                )
            await admin_channel.send(embed=embed)


def _is_exempt(member: discord.Member) -> bool:
    """Return True if the member holds an admin/mod role and should skip automod."""
    return any(r.name.lower() in EXEMPT_ROLE_NAMES for r in member.roles)


def _check_spam(user_id: int, channel_id: int, content: str) -> str | None:
    """
    Record the new message and return a human-readable spam reason if the
    user is spamming, or None if everything looks fine.
    """
    now = datetime.now(timezone.utc)
    key = (user_id, channel_id)

    # --- record timestamp ---
    ts_queue = _message_timestamps[key]
    ts_queue.append(now)

    # --- record content ---
    ct_queue = _message_contents[key]
    ct_queue.append((content, now))

    # Prune entries older than the longest threshold window so the deques
    # don't grow unboundedly between cleanup cycles.
    max_window = max(w for _, w in SPAM_THRESHOLDS)
    while ts_queue and (now - ts_queue[0]).total_seconds() > max_window:
        ts_queue.popleft()
    while ct_queue and (now - ct_queue[0][1]).total_seconds() > max_window:
        ct_queue.popleft()

    # --- threshold checks ---
    for max_msgs, window in SPAM_THRESHOLDS:
        recent = sum(
            1 for t in ts_queue
            if (now - t).total_seconds() <= window
        )
        if recent > max_msgs:
            return f"{recent} messages in {window}s (limit: {max_msgs})"

    # --- duplicate-message check ---
    recent_dupes = sum(
        1 for c, t in ct_queue
        if c == content and (now - t).total_seconds() <= DUPLICATE_WINDOW
    )
    # The message itself is already in the queue, so > 1 means it's a repeat.
    if recent_dupes > 1:
        return f"repeated identical message within {DUPLICATE_WINDOW}s"

    return None


async def _punish_spammer(
    message: discord.Message,
    reason: str,
    spam_messages: list[discord.Message],
) -> None:
    """Timeout the spammer, delete their spam, and log to the admin channel."""
    member = message.author
    guild = message.guild

    # Apply 10-minute timeout
    until = discord.utils.utcnow() + timedelta(minutes=AUTOMOD_TIMEOUT_MINUTES)
    try:
        await member.timeout(until, reason=f"[Automod] {reason}")
    except discord.Forbidden:
        pass  # Bot lacks permission — log anyway

    # Bulk-delete spam messages (discord allows bulk delete for messages < 14 days)
    try:
        await message.channel.delete_messages(spam_messages)
    except (discord.Forbidden, discord.HTTPException):
        # Fall back to individual deletes
        for msg in spam_messages:
            try:
                await msg.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

    # Log to admin channel
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        embed = discord.Embed(
            title="🛡️ Automod — Spam Detected",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="User",
            value=f"{member} (ID: `{member.id}`)",
            inline=False,
        )
        embed.add_field(
            name="Channel",
            value=message.channel.mention,
            inline=True,
        )
        embed.add_field(
            name="Reason",
            value=reason,
            inline=True,
        )
        embed.add_field(
            name="Action",
            value=f"Timed out for {AUTOMOD_TIMEOUT_MINUTES} minutes · {len(spam_messages)} message(s) deleted",
            inline=False,
        )
        embed.set_footer(text=f"Guild: {guild.name}")
        await admin_channel.send(embed=embed)

    # Record punishment time to enforce cooldown
    _punishment_cooldowns[member.id] = discord.utils.utcnow()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    # Attempt database connection with error handling
    try:
        bot.db = await asyncpg.connect(os.getenv("DATABASE_URL"))
        print("✅ Connected to PostgreSQL")

        await bot.db.execute("""
            CREATE TABLE IF NOT EXISTS economy (
                user_id BIGINT PRIMARY KEY,
                robux BIGINT NOT NULL DEFAULT 0,
                last_daily TIMESTAMP
            )
        """)
        print("✅ Economy table ready")
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        print("⚠️ Bot is running but economy commands will not work until database is available")
        bot.db = None

    cleanup_automod_state.start()

@tasks.loop(minutes=5)
async def cleanup_automod_state():
    """Periodically evict stale entries from the automod tracking dicts."""
    now = datetime.now(timezone.utc)
    max_window = max(w for _, w in SPAM_THRESHOLDS)

    stale_ts = [k for k, q in _message_timestamps.items() if not q or (now - q[-1]).total_seconds() > max_window]
    for k in stale_ts:
        del _message_timestamps[k]

    stale_ct = [k for k, q in _message_contents.items() if not q or (now - q[-1][1]).total_seconds() > max_window]
    for k in stale_ct:
        del _message_contents[k]

    stale_cd = [uid for uid, t in _punishment_cooldowns.items() if (now - t).total_seconds() > PUNISHMENT_COOLDOWN]
    for uid in stale_cd:
        del _punishment_cooldowns[uid]


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # ------------------------------------------------------------------
    # Automod — only applies to guild (server) text channels
    # ------------------------------------------------------------------
    if (
        isinstance(message.channel, discord.TextChannel)
        and isinstance(message.author, discord.Member)
        and not _is_exempt(message.author)
    ):
        spam_reason = _check_spam(message.author.id, message.channel.id, message.content)

        if spam_reason:
            user_id = message.author.id
            now = discord.utils.utcnow()

            # Enforce per-user punishment cooldown
            last_punished = _punishment_cooldowns.get(user_id)
            on_cooldown = (
                last_punished is not None
                and (now - last_punished).total_seconds() < PUNISHMENT_COOLDOWN
            )

            if not on_cooldown:
                # Collect all cached messages from this user in this channel
                # to bulk-delete alongside the triggering message.
                key = (user_id, message.channel.id)
                spam_msgs = [message]

                # Fetch recent channel history to find the user's spam messages
                try:
                    async for hist_msg in message.channel.history(limit=20):
                        if (
                            hist_msg.author.id == user_id
                            and hist_msg.id != message.id
                        ):
                            spam_msgs.append(hist_msg)
                except (discord.Forbidden, discord.HTTPException):
                    pass

                await _punish_spammer(message, spam_reason, spam_msgs)

            # Stop processing — don't run commands or modmail for spam messages
            return

    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id

        # If this user is mid-application, collect their answer instead of
        # treating the message as regular modmail.
        if user_id in pending_applications:
            app = pending_applications[user_id]
            step = app["step"]

            # Record the answer for the current question
            app["answers"].append(message.content)
            step += 1
            app["step"] = step

            if step < len(APPLICATION_QUESTIONS):
                # Ask the next question
                await message.channel.send(APPLICATION_QUESTIONS[step])
            else:
                # All questions answered — show the Submit button
                view = SubmitApplicationView(user_id)
                await message.channel.send(
                    "✅ All done! Click **Submit** to send your application to staff.",
                    view=view,
                )
            return  # Do NOT forward application answers to modmail

        # Regular modmail flow
        await message.channel.send("✅ Message Received!")

        modmail_channel = bot.get_channel(MODMAIL_CHANNEL_ID)

        if modmail_channel:
            embed = discord.Embed(title="📨 New ModMail", color=discord.Color.blue())
            embed.add_field(
                name="User",
                value=f"{message.author} ({message.author.id})",
                inline=False
            )
            embed.add_field(
                name="Message",
                value=message.content or "[No text]",
                inline=False
            )
            await modmail_channel.send(embed=embed)

    await bot.process_commands(message)

@bot.command()
@commands.has_permissions(administrator=True)
async def reply(ctx, user_id: int, *, message):
    user = await bot.fetch_user(user_id)
    await user.send(f"📬 Staff Reply\n\n{message}")
    await ctx.send("✅ Reply sent.")

@bot.command()
@commands.has_permissions(administrator=True)
async def echo(ctx, *, message):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    try:
        await ctx.send(message)
    except discord.Forbidden:
        try:
            await ctx.author.send("❌ I don't have permissions to send in the channel")
        except discord.Forbidden:
            pass

@echo.error
async def echo_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have admin permissions")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Please provide a message to echo.")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"✅ Kicked {member}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    dm_message = (
        f"You're banned from Roblox Fans Server! "
        f"Reason: {reason} "
        f"Join this server to appeal your ban! https://discord.gg/jyuC9nuFST"
    )

    try:
        await member.send(dm_message)
    except discord.Forbidden:
        # Member has DMs disabled or has blocked the bot — log it and continue.
        print(f"⚠️ Could not DM {member} before banning (DMs disabled/blocked).")

    await member.ban(reason=reason)
    await ctx.send(f"✅ Banned {member}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, minutes: int, *, reason="No reason provided"):
    until = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await ctx.send(f"✅ Timed out {member} for {minutes} minute(s)")

@bot.command()
async def apply(ctx):
    """Start a mod application. The bot will DM you a series of questions."""
    user = ctx.author
    user_id = user.id

    # If the user already has an application in progress, don't start a new one
    if user_id in pending_applications:
        await ctx.send(
            f"⚠️ {user.mention}, you already have an application in progress. "
            "Please check your DMs and finish answering the questions.",
            delete_after=10,
        )
        return

    # Acknowledge in the channel (works in both guild channels and DMs)
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(
            f"📬 {user.mention}, I've sent you a DM with the application questions!",
            delete_after=10,
        )

    # Open a DM and send the first question
    try:
        dm = await user.create_dm()
        await dm.send(
            "👋 Welcome to the **Mod Application**!\n\n"
            f"**Q1:** {APPLICATION_QUESTIONS[0]}"
        )
    except discord.Forbidden:
        await ctx.send(
            f"❌ {user.mention}, I couldn't send you a DM. "
            "Please enable DMs from server members and try again."
        )
        return

    # Register the session — step 0 means we're waiting for the answer to Q1
    pending_applications[user_id] = {"step": 0, "answers": []}

# ---------------------------------------------------------------------------
# Economy system
# ---------------------------------------------------------------------------
DAILY_REWARD = 100_000


async def get_robux(user_id: int) -> int:
    if bot.db is None:
        raise RuntimeError("Database is not connected. Try again later.")

    row = await bot.db.fetchrow(
        "SELECT robux FROM economy WHERE user_id = $1",
        user_id
    )

    if row is None:
        await bot.db.execute(
            "INSERT INTO economy (user_id, robux) VALUES ($1, 0)",
            user_id
        )
        return 0

    return row["robux"]


async def add_robux(user_id: int, amount: int) -> None:
    if bot.db is None:
        raise RuntimeError("Database is not connected. Try again later.")

    balance = await get_robux(user_id)

    await bot.db.execute("""
        INSERT INTO economy (user_id, robux)
        VALUES ($1, $2)
        ON CONFLICT (user_id)
        DO UPDATE SET robux = $2
    """, user_id, balance + amount)


@bot.command()
async def daily(ctx):
    """Claim a daily reward of 100,000 Robux (once every 24 hours)."""
    user_id = ctx.author.id
    now = datetime.now(timezone.utc)

    row = await bot.db.fetchrow(
        "SELECT last_daily FROM economy WHERE user_id = $1",
        user_id
    )

    if row and row["last_daily"] is not None:
        last_claim = row["last_daily"]
        if last_claim.tzinfo is None:
            last_claim = last_claim.replace(tzinfo=timezone.utc)
        elapsed = now - last_claim
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            await ctx.send(
                f"⏳ {ctx.author.mention}, you already claimed your daily reward! "
                f"Come back in **{hours}h {minutes}m**."
            )
            return

    await add_robux(user_id, DAILY_REWARD)
    await bot.db.execute(
        "UPDATE economy SET last_daily = $1 WHERE user_id = $2",
        now, user_id
    )
    balance = await get_robux(user_id)
    await ctx.send(
        f"🎁 {ctx.author.mention}, you received **{DAILY_REWARD:,} Robux**!\n"
        f"💰 Balance: **{balance:,} Robux**"
    )


@bot.command()
async def robux(ctx):
    """Check your current Robux balance."""
    balance = await get_robux(ctx.author.id)
    await ctx.send(
        f"💰 {ctx.author.mention}, you currently have **{balance:,} Robux**."
    )


@bot.command()
async def cf(ctx, amount: int):
    """Flip a coin and gamble Robux. Usage: !cf <amount>"""
    user_id = ctx.author.id

    if amount <= 0:
        await ctx.send("❌ Please enter a valid amount of Robux greater than 0.")
        return

    balance = await get_robux(user_id)

    if balance < amount:
        await ctx.send(
            f"❌ {ctx.author.mention}, you don't have enough Robux! "
            f"Your balance is **{balance:,} Robux**."
        )
        return

    result = random.choice(["Heads", "Tails"])

    if result == "Heads":
        winnings = amount
        await add_robux(user_id, winnings)
        balance = await get_robux(user_id)
        await ctx.send(
            f"🪙 **Heads!** You won **{winnings:,} Robux**!\n"
            f"💰 Balance: **{balance:,} Robux**"
        )
    else:
        await add_robux(user_id, -amount)
        balance = await get_robux(user_id)
        await ctx.send(
            f"🪙 **Tails!** You lost **{amount:,} Robux**!\n"
            f"💰 Balance: **{balance:,} Robux**"
        )


@cf.error
async def cf_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage: `!cf <amount>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Please provide a whole number as the amount.")


@bot.command()
async def transfer(ctx, member: discord.Member, amount: int):
    """Transfer Robux to another member. Usage: !transfer <@member> <amount>"""
    sender_id = ctx.author.id
    receiver_id = member.id

    if member.bot:
        await ctx.send("❌ You cannot transfer Robux to bots.")
        return

    if member.id == sender_id:
        await ctx.send("❌ You cannot transfer Robux to yourself.")
        return

    if amount <= 0:
        await ctx.send("❌ Please enter a valid amount greater than 0.")
        return

    sender_balance = await get_robux(sender_id)

    if sender_balance < amount:
        await ctx.send(
            f"❌ {ctx.author.mention}, you don't have enough Robux! "
            f"Your balance is **{sender_balance:,} Robux**."
        )
        return

    await add_robux(sender_id, -amount)
    await add_robux(receiver_id, amount)
    new_balance = await get_robux(sender_id)

    await ctx.send(
        f"💸 {ctx.author.mention} transferred **{amount:,} Robux** to {member.mention}!\n"
        f"💰 Your new balance: **{new_balance:,} Robux**"
    )


@transfer.error
async def transfer_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage: `!transfer <@member> <amount>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Please mention a valid member and provide a whole number amount.")


@bot.event
async def on_command_error(ctx, error):
    """Catch database connection errors and other command errors."""
    if isinstance(error, commands.CommandInvokeError):
        # Check if it's a database error
        if "Database is not connected" in str(error.original):
            await ctx.send("❌ Database is temporarily unavailable. Try again in a moment.")
        elif "bot.db" in str(error.original) or "AttributeError" in str(error.original):
            await ctx.send("❌ Database connection failed. Try again in a moment.")
        else:
            await ctx.send(f"❌ An error occurred: {error.original}")


# ---------------------------------------------------------------------------
# Owner-only admin commands
# ---------------------------------------------------------------------------
# Discord user IDs of the bot owner(s) — only these users may run owner-only commands.
OWNER_ID = 1390936694731309076
ADDITIONAL_ADMIN_ID = 1523645250197782651
AUTHORIZED_BALANCE_ADMIN_IDS = {OWNER_ID, ADDITIONAL_ADMIN_ID}


@bot.group(name="change", invoke_without_command=True)
async def change(ctx):
    """Parent command group for owner-only balance modifications."""
    await ctx.send("❌ Usage: `!change balance <amount> <username>`")


@change.command(name="balance")
async def change_balance(ctx, amount: str = None, *, username: str = None):
    """
    Usage: !change balance <amount> <username>
    Example: !change balance 5000 john_doe
    """
    # --- Permission check ---
    if ctx.author.id not in AUTHORIZED_BALANCE_ADMIN_IDS:
        await ctx.send("You do not have permission to use this command.")
        return

    # --- Argument validation ---
    if amount is None or username is None:
        await ctx.send("❌ Usage: `!change balance <amount> <username>`")
        return

    try:
        parsed_amount = float(amount)
    except (TypeError, ValueError):
        await ctx.send(f"❌ Invalid amount: `{amount}`. Please provide a valid number.")
        return

    # Store whole Robux amounts as ints when possible, otherwise keep the float.
    new_balance = int(parsed_amount) if parsed_amount.is_integer() else parsed_amount

    # --- Find the user by username/display name in the guild ---
    member = None
    if ctx.guild:
        username_lower = username.lower()
        member = discord.utils.find(
            lambda m: (
                m.name.lower() == username_lower
                or m.display_name.lower() == username_lower
                or str(m).lower() == username_lower
            ),
            ctx.guild.members,
        )

    if member is None:
        await ctx.send(f"❌ Could not find a user named '{username}'.")
        return

    # --- Update the balance ---
    try:
        await bot.db.execute(
            "INSERT INTO economy (user_id, robux) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET robux = $2",
            member.id, new_balance
        )
    except Exception as e:
        await ctx.send(f"❌ Failed to update balance: {e}")
        return

    await ctx.send(f"✅ Updated {username}'s balance to {new_balance} Robux")


@change_balance.error
async def change_balance_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage: `!change balance <amount> <username>`")
    else:
        await ctx.send(f"❌ An error occurred: {error}")


# ---------------------------------------------------------------------------
# Public balance lookup command
# ---------------------------------------------------------------------------
@bot.group(name="check", invoke_without_command=True)
async def check(ctx):
    """Parent command group for public balance lookups."""
    await ctx.send("❌ Usage: `!check balance <username>`")


@check.command(name="balance")
async def check_balance(ctx, *, username: str = None):
    """
    Usage: !check balance <username>
    Example: !check balance john_doe
    """
    # --- Argument validation ---
    if username is None:
        await ctx.send("❌ Usage: `!check balance <username>`")
        return

    # --- Find the user by username/display name in the guild ---
    member = None
    if ctx.guild:
        username_lower = username.lower()
        member = discord.utils.find(
            lambda m: (
                m.name.lower() == username_lower
                or m.display_name.lower() == username_lower
                or str(m).lower() == username_lower
            ),
            ctx.guild.members,
        )

    if member is None:
        await ctx.send(f"❌ Could not find a user named '{username}'.")
        return

    # --- Fetch the balance ---
    try:
        balance = await get_robux(member.id)
    except Exception as e:
        await ctx.send(f"❌ Failed to fetch balance: {e}")
        return

    await ctx.send(f"✅ {username}'s balance: {balance} Robux")


@check_balance.error
async def check_balance_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage: `!check balance <username>`")
    else:
        await ctx.send(f"❌ An error occurred: {error}")



@bot.command(name="leaderboard", aliases=["lb"])
async def leaderboard(ctx):
    """Global Robux leaderboard."""

    rows = await bot.db.fetch("""
        SELECT user_id, robux
        FROM economy
        ORDER BY robux DESC
        """)

    if not rows:
        await ctx.send("🏆 No users have any Robux yet.")
        return

    lines = []

    for i, row in enumerate(rows, start=1):
        uid = row["user_id"]
        balance = row["robux"]

        try:
            user = await bot.fetch_user(uid)
            name = user.name
        except Exception:
            name = f"Unknown User ({uid})"

        if i == 1:
            suffix = "st"
        elif i == 2:
            suffix = "nd"
        elif i == 3:
            suffix = "rd"
        else:
            suffix = "th"

        lines.append(f"{i}{suffix} {name} - {balance:,} Robux")

    await ctx.send(
        "🏆 **Global Robux Leaderboard**\n\n" +
        "\n".join(lines)
    )

print("Starting Flask API...")

Thread(target=run_web, daemon=True).start()

bot.run(os.getenv("DISCORD_TOKEN"))
