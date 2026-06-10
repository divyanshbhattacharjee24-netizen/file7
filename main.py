import os
import discord
from discord.ext import commands
from datetime import timedelta

MODMAIL_CHANNEL_ID = 1393229169751752766

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
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

bot.run(os.getenv("DISCORD_TOKEN"))
