import discord
from discord.ext import commands
from datetime import datetime, timedelta
import random

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="rb ", intents=intents)

robux = {}
last_daily = {}

DAILY_REWARD = 100000

def get_robux(user_id):
return robux.get(user_id, 0)

def add_robux(user_id, amount):
robux[user_id] = get_robux(user_id) + amount

@bot.event
async def on_ready():
print(f"Logged in as {bot.user}")

@bot.command()
async def daily(ctx):
user_id = ctx.author.id
now = datetime.utcnow()

if user_id in last_daily:
    if now - last_daily[user_id] < timedelta(hours=24):
        await ctx.send("No cheating of daily reward hacks!")
        return

add_robux(user_id, DAILY_REWARD)
last_daily[user_id] = now

await ctx.send(
    f"🎁 {ctx.author.mention}, you received {DAILY_REWARD:,} Robux!\n"
    f"💰 Balance: {get_robux(user_id):,} Robux"
)

@bot.command()
async def robux(ctx):
balance = get_robux(ctx.author.id)

await ctx.send(
    f"{ctx.author.mention}, you currently have {balance:,} Robux."
)

@bot.command()
async def cf(ctx, amount: int):
user_id = ctx.author.id

if amount <= 0:
    await ctx.send("Please enter a valid amount of Robux.")
    return

if get_robux(user_id) < amount:
    await ctx.send(
        f"Nope, {ctx.author.name} do you think you have enough Robux?"
    )
    return

result = random.choice(["Heads", "Tails"])

if result == "Heads":
    winnings = amount * 2

    add_robux(user_id, winnings)

    await ctx.send(
        f"🪙 Heads!\n"
        f"You won {winnings:,} Robux!\n"
        f"💰 Balance: {get_robux(user_id):,} Robux"
    )

else:
    add_robux(user_id, -amount)

    await ctx.send(
        f"🪙 Tails!\n"
        f"You lost {amount:,} Robux!\n"
        f"💰 Balance: {get_robux(user_id):,} Robux"
    )

@bot.command()
async def transfer(ctx, amount: int, member: discord.Member):
sender_id = ctx.author.id
receiver_id = member.id

if member.bot:
    await ctx.send("You cannot transfer Robux to bots.")
    return

if amount <= 0:
    await ctx.send("Please enter a valid amount.")
    return

if get_robux(sender_id) < amount:
    await ctx.send(
        f"Nope, {ctx.author.name} do you think you have enough Robux?"
    )
    return

add_robux(sender_id, -amount)
add_robux(receiver_id, amount)

await ctx.send(
    f"💸 {ctx.author.mention} transferred "
    f"{amount:,} Robux to {member.mention}!"
)

bot.run("YOUR_BOT_TOKEN")
