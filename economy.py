import discord
from discord.ext import commands
from datetime import datetime, timedelta
import random

class Economy(commands.Cog):
def init(self, bot):
self.bot = bot
self.robux = {}
self.last_daily = {}
self.DAILY_REWARD = 100000

def get_robux(self, user_id):
    return self.robux.get(user_id, 0)

def add_robux(self, user_id, amount):
    self.robux[user_id] = self.get_robux(user_id) + amount

@commands.command()
async def daily(self, ctx):
    user_id = ctx.author.id
    now = datetime.utcnow()

    if user_id in self.last_daily:
        if now - self.last_daily[user_id] < timedelta(hours=24):
            await ctx.send("No cheating of daily reward hacks!")
            return

    self.add_robux(user_id, self.DAILY_REWARD)
    self.last_daily[user_id] = now

    await ctx.send(
        f"🎁 {ctx.author.mention}, you received {self.DAILY_REWARD:,} Robux!\n"
        f"💰 Balance: {self.get_robux(user_id):,} Robux"
    )

@commands.command()
async def robux(self, ctx):
    balance = self.get_robux(ctx.author.id)

    await ctx.send(
        f"{ctx.author.mention}, you currently have {balance:,} Robux."
    )

@commands.command()
async def cf(self, ctx, amount: int):
    user_id = ctx.author.id

    if amount <= 0:
        await ctx.send("Please enter a valid amount of Robux.")
        return

    if self.get_robux(user_id) < amount:
        await ctx.send(
            f"Nope, {ctx.author.name} do you think you have enough Robux?"
        )
        return

    result = random.choice(["Heads", "Tails"])

    if result == "Heads":
        winnings = amount * 2

        self.add_robux(user_id, winnings)

        await ctx.send(
            f"🪙 Heads!\n"
            f"You won {winnings:,} Robux!\n"
            f"💰 Balance: {self.get_robux(user_id):,} Robux"
        )
    else:
        self.add_robux(user_id, -amount)

        await ctx.send(
            f"🪙 Tails!\n"
            f"You lost {amount:,} Robux!\n"
            f"💰 Balance: {self.get_robux(user_id):,} Robux"
        )

@commands.command()
async def transfer(self, ctx, amount: int, member: discord.Member):
    sender_id = ctx.author.id
    receiver_id = member.id

    if member.bot:
        await ctx.send("You cannot transfer Robux to bots.")
        return

    if amount <= 0:
        await ctx.send("Please enter a valid amount.")
        return

    if self.get_robux(sender_id) < amount:
        await ctx.send(
            f"Nope, {ctx.author.name} do you think you have enough Robux?"
        )
        return

    self.add_robux(sender_id, -amount)
    self.add_robux(receiver_id, amount)

    await ctx.send(
        f"💸 {ctx.author.mention} transferred "
        f"{amount:,} Robux to {member.mention}!"
    )

async def setup(bot):
await bot.add_cog(Economy(bot))
