import discord

async def send_ban_dm(user: discord.User):
    message = (
        "You've Been Banned from Roblox Fans Server for breaking the rules.\n\n"
        "You may reappeal and get unbanned by joining this server and appealing your ban https://discord.gg/jyuC9nuFST:\n"
        "https://discord.gg/jyuC9nuFST"
    )

    try:
        await user.send(message)
    except Exception as e:
        print(f"Failed to DM banned user {user}: {e}")
