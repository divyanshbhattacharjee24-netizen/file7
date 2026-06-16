import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    try:
        await member.send(
            "You've been banned in Roblox Fans for breaking the rules. "
            "Join the server to appeal your ban and get unbanned https://discord.gg/jyuC9nuFST"
        )
    except discord.Forbidden:
        await ctx.send("Couldn't DM the user (DMs are closed).")

    await member.ban(reason=reason)
    await ctx.send(f"{member} has been banned.")

bot.run("YOUR_BOT_TOKEN")
