import os
import discord
from discord.ext import commands
from datetime import timedelta

MODMAIL_CHANNEL_ID = 1393229169751752766

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
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
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"✅ Kicked {member}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
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

bot.run(os.getenv("DISCORD_TOKEN"))
