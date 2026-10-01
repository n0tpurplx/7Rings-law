import os
import asyncio
import discord
from discord import app_commands
from aiohttp import web

from database import Database
from webhook import create_webhook_app


# =========================
# CONFIG
# =========================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

WHITELIST_ROLE_ID = 1555221290837614642
SUPERVISOR_ROLE_ID = 1555232185450238083
DEPARTMENT_MANAGER_ROLE_ID = 1555232542767190177

WARRANT_CONFIRMATION_CHANNEL_ID = 1555233076056039524
FINE_CONFIRMATION_CHANNEL_ID = 1555233224052187297

DEPARTMENT_NAME = "State Patrol"

WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))


if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not configured.")


# =========================
# DATABASE
# =========================

db = Database("cad.db")


# =========================
# DISCORD BOT
# =========================

class CADBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")
        print(f"Department: {DEPARTMENT_NAME}")
        print("CAD bot is ready.")


bot = CADBot()


# =========================
# GLOBAL PERMISSION CHECK
# =========================

async def whitelist_check(interaction: discord.Interaction) -> bool:
    """
    Every CAD command uses this check.

    ONLY the exact whitelist role grants access.
    Administrator permissions do not bypass it.
    """

    if not isinstance(interaction.user, discord.Member):
        return False

    return any(
        role.id == WHITELIST_ROLE_ID
        for role in interaction.user.roles
    )


def require_whitelist():
    return app_commands.check(whitelist_check)


# =========================
# /dp
# =========================

@bot.tree.command(
    name="dp",
    description="Change your State Patrol CAD status."
)
@app_commands.describe(
    status="Your new CAD status, for example 10-8 or 10-7."
)
@require_whitelist()
async def dp(
    interaction: discord.Interaction,
    status: str
):
    status = status.strip().upper()

    if not status:
        await interaction.response.send_message(
            "You need to provide a status.",
            ephemeral=True
        )
        return

    if not status.startswith("10-"):
        await interaction.response.send_message(
            "Invalid status format. Example: `10-8`.",
            ephemeral=True
        )
        return

    # We currently don't know the exact ER:LC webhook
    # identity fields yet, so Discord ID is used here.
    db.set_unit_status(
        discord_id=interaction.user.id,
        discord_name=str(interaction.user),
        status=status,
        department=DEPARTMENT_NAME
    )

    await interaction.response.send_message(
        f"Your State Patrol status is now **{status}**.",
        ephemeral=True
    )


# =========================
# /units
# =========================

@bot.tree.command(
    name="units",
    description="View active State Patrol units."
)
@require_whitelist()
async def units(interaction: discord.Interaction):

    active_units = db.get_active_units()

    if not active_units:
        await interaction.response.send_message(
            "There are currently no State Patrol units with an active CAD status.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="State Patrol Units",
        description="Current CAD unit statuses.",
        color=discord.Color.dark_blue()
    )

    for unit in active_units:
        embed.add_field(
            name=unit["discord_name"],
            value=(
                f"Status: **{unit['status']}**\n"
                f"Updated: <t:{unit['updated_at']}:R>"
            ),
            inline=False
        )

    await interaction.response.send_message(
        embed=embed
    )


# =========================
# COMMAND ERROR HANDLER
# =========================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(error, app_commands.CheckFailure):
        message = (
            "You are not authorized to use this CAD bot."
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )

        return

    print(f"Command error: {repr(error)}")

    if interaction.response.is_done():
        await interaction.followup.send(
            "An internal error occurred.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "An internal error occurred.",
            ephemeral=True
        )


# =========================
# WEB SERVER
# =========================

async def start_web_server():
    app = create_webhook_app(db)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        WEBHOOK_HOST,
        WEBHOOK_PORT
    )

    await site.start()

    print(
        f"Webhook server listening on "
        f"{WEBHOOK_HOST}:{WEBHOOK_PORT}"
    )

    return runner


# =========================
# MAIN
# =========================

async def main():
    runner = await start_web_server()

    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
