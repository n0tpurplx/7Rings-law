import os
import time
import sqlite3
import asyncio

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands


# =====================================================
# CONFIGURATION
# =====================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

WHITELIST_ROLE_ID = 1555221290837614642
SUPERVISOR_ROLE_ID = 1555232185450238083
DEPARTMENT_MANAGER_ROLE_ID = 1555232542767190177

WARRANT_CONFIRMATION_CHANNEL_ID = 1555233076056039524
FINE_CONFIRMATION_CHANNEL_ID = 1555233224052187297

DEPARTMENT = "State Patrol"
DB_PATH = "cad.db"

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")


# =====================================================
# DATABASE
# =====================================================

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.execute("PRAGMA journal_mode=WAL")

db.executescript(
    """
    CREATE TABLE IF NOT EXISTS units (
        discord_id INTEGER PRIMARY KEY,
        callsign TEXT UNIQUE,
        status TEXT NOT NULL DEFAULT '10-7',
        department TEXT NOT NULL DEFAULT 'State Patrol',
        joined_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS bolos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model TEXT NOT NULL,
        color TEXT NOT NULL,
        plate TEXT NOT NULL,
        description TEXT,
        issuer_id INTEGER NOT NULL,
        issuer_name TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS fines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id INTEGER NOT NULL,
        issuer_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL,
        decided_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS warrants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_name TEXT NOT NULL,
        armed INTEGER NOT NULL,
        reason TEXT NOT NULL,
        requester_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'awaiting_requester',
        created_at INTEGER NOT NULL,
        decided_at INTEGER,
        supervisor_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        details TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """
)

db.commit()


def audit(actor_id, action, details):
    db.execute(
        """
        INSERT INTO audit_logs
        (actor_id, action, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            actor_id,
            action,
            details,
            int(time.time()),
        ),
    )
    db.commit()


def timestamp(value):
    return f"<t:{value}:R>"


# =====================================================
# BOT
# =====================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# =====================================================
# PERMISSIONS
# =====================================================

def has_role(member, role_id):
    return (
        isinstance(member, discord.Member)
        and any(role.id == role_id for role in member.roles)
    )


async def whitelist_check(interaction):
    return has_role(interaction.user, WHITELIST_ROLE_ID)


async def manager_check(interaction):
    return (
        has_role(interaction.user, WHITELIST_ROLE_ID)
        and has_role(interaction.user, DEPARTMENT_MANAGER_ROLE_ID)
    )


async def supervisor_check(interaction):
    return (
        has_role(interaction.user, WHITELIST_ROLE_ID)
        and has_role(interaction.user, SUPERVISOR_ROLE_ID)
    )


def cad_command(**kwargs):
    def decorator(func):
        return app_commands.check(whitelist_check)(
            app_commands.command(**kwargs)(func)
        )

    return decorator


def manager_command(**kwargs):
    def decorator(func):
        return app_commands.check(manager_check)(
            app_commands.command(**kwargs)(func)
        )

    return decorator


def supervisor_command(**kwargs):
    def decorator(func):
        return app_commands.check(supervisor_check)(
            app_commands.command(**kwargs)(func)
        )

    return decorator


# =====================================================
# STATE PATROL STATUS
# =====================================================

STATUS_CHOICES = [
    app_commands.Choice(
        name="10-8 | In Service",
        value="10-8",
    ),
    app_commands.Choice(
        name="10-7 | Out of Service",
        value="10-7",
    ),
    app_commands.Choice(
        name="10-6 | Busy",
        value="10-6",
    ),
    app_commands.Choice(
        name="10-23 | On Scene",
        value="10-23",
    ),
    app_commands.Choice(
        name="10-11 | Traffic Stop",
        value="10-11",
    ),
    app_commands.Choice(
        name="10-15 | Transporting",
        value="10-15",
    ),
]


@cad_command(
    name="dp",
    description="Change your State Patrol status.",
)
@app_commands.describe(status="Your new unit status")
@app_commands.choices(status=STATUS_CHOICES)
async def dp(
    interaction: discord.Interaction,
    status: app_commands.Choice[str],
):
    row = db.execute(
        "SELECT * FROM units WHERE discord_id = ?",
        (interaction.user.id,),
    ).fetchone()

    if not row:
        await interaction.response.send_message(
            "You are not registered as a State Patrol unit. "
            "Ask a department manager to add you.",
            ephemeral=True,
        )
        return

    db.execute(
        "UPDATE units SET status = ? WHERE discord_id = ?",
        (
            status.value,
            interaction.user.id,
        ),
    )
    db.commit()

    audit(
        interaction.user.id,
        "STATUS_CHANGE",
        status.value,
    )

    await interaction.response.send_message(
        f"Your State Patrol status is now **{status.value}**.",
        ephemeral=True,
    )


# =====================================================
# UNIT ROSTER
# =====================================================

async def build_units_embed(guild):
    rows = db.execute(
        "SELECT * FROM units ORDER BY callsign"
    ).fetchall()

    if not rows:
        return None

    embed = discord.Embed(
        title="State Patrol | Unit Roster",
        color=discord.Color.dark_blue(),
    )

    for row in rows:
        member = guild.get_member(row["discord_id"])

        if member is None:
            try:
                member = await guild.fetch_member(row["discord_id"])
            except (discord.NotFound, discord.HTTPException):
                member = None

        name = (
            member.display_name
            if member
            else f"User {row['discord_id']}"
        )

        embed.add_field(
            name=f"{row['callsign']} | {name}",
            value=f"Status: **{row['status']}**",
            inline=False,
        )

    return embed


@cad_command(
    name="units",
    description="View State Patrol units.",
)
async def units(interaction: discord.Interaction):
    embed = await build_units_embed(interaction.guild)

    if embed is None:
        await interaction.response.send_message(
            "No State Patrol personnel have been registered.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(embed=embed)


# =====================================================
# DEPARTMENT STAFF MANAGEMENT
# =====================================================

@manager_command(
    name="staff_add",
    description="Register a State Patrol member.",
)
@app_commands.describe(
    member="Discord member",
    callsign="Their assigned callsign",
)
async def staff_add(
    interaction: discord.Interaction,
    member: discord.Member,
    callsign: str,
):
    callsign = callsign.strip().upper()

    if not callsign or len(callsign) > 20:
        await interaction.response.send_message(
            "Please provide a valid callsign.",
            ephemeral=True,
        )
        return

    try:
        db.execute(
            """
            INSERT INTO units
            (discord_id, callsign, status, department, joined_at)
            VALUES (?, ?, '10-7', ?, ?)
            ON CONFLICT(discord_id)
            DO UPDATE SET callsign = excluded.callsign
            """,
            (
                member.id,
                callsign,
                DEPARTMENT,
                int(time.time()),
            ),
        )

        db.commit()

    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "That callsign is already assigned to another unit.",
            ephemeral=True,
        )
        return

    audit(
        interaction.user.id,
        "STAFF_ADDED",
        f"{member.id} assigned {callsign}",
    )

    await interaction.response.send_message(
        f"Added {member.mention} to State Patrol as **{callsign}**."
    )


@manager_command(
    name="staff_remove",
    description="Remove a member from the State Patrol roster.",
)
async def staff_remove(
    interaction: discord.Interaction,
    member: discord.Member,
):
    cursor = db.execute(
        "DELETE FROM units WHERE discord_id = ?",
        (member.id,),
    )

    db.commit()

    if cursor.rowcount == 0:
        await interaction.response.send_message(
            "That member is not registered.",
            ephemeral=True,
        )
        return

    audit(
        interaction.user.id,
        "STAFF_REMOVED",
        str(member.id),
    )

    await interaction.response.send_message(
        f"Removed {member.mention} from the State Patrol roster."
    )


@manager_command(
    name="staff_list",
    description="View the State Patrol roster.",
)
async def staff_list(interaction: discord.Interaction):
    embed = await build_units_embed(interaction.guild)

    if embed is None:
        await interaction.response.send_message(
            "No State Patrol personnel have been registered.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(embed=embed)


# =====================================================
# BOLO SYSTEM
# =====================================================

@cad_command(
    name="bolo",
    description="Create a vehicle BOLO.",
)
@app_commands.describe(
    model="Vehicle model",
    color="Vehicle color",
    plate="License plate",
    description="Additional identifying information",
)
async def bolo(
    interaction: discord.Interaction,
    model: str,
    color: str,
    plate: str,
    description: str = "No additional information",
):
    cursor = db.execute(
        """
        INSERT INTO bolos
        (
            model,
            color,
            plate,
            description,
            issuer_id,
            issuer_name,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model,
            color,
            plate.upper(),
            description,
            interaction.user.id,
            interaction.user.display_name,
            int(time.time()),
        ),
    )

    db.commit()

    bolo_id = cursor.lastrowid

    audit(
        interaction.user.id,
        "BOLO_CREATED",
        f"BOLO #{bolo_id}: {model}, {color}, {plate}",
    )

    embed = discord.Embed(
        title=f"Active BOLO #{bolo_id}",
        color=discord.Color.orange(),
    )

    embed.add_field(
        name="Vehicle",
        value=model,
        inline=True,
    )

    embed.add_field(
        name="Color",
        value=color,
        inline=True,
    )

    embed.add_field(
        name="Plate",
        value=plate.upper(),
        inline=True,
    )

    embed.add_field(
        name="Description",
        value=description,
        inline=False,
    )

    embed.add_field(
        name="Issued By",
        value=interaction.user.mention,
    )

    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(
        embed=embed
    )


@cad_command(
    name="bolos",
    description="View active BOLOs.",
)
async def bolos(interaction: discord.Interaction):
    rows = db.execute(
        """
        SELECT *
        FROM bolos
        WHERE active = 1
        ORDER BY created_at DESC
        """
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "There are no active BOLOs.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Active State Patrol BOLOs",
        color=discord.Color.orange(),
    )

    for row in rows[:25]:
        embed.add_field(
            name=f"BOLO #{row['id']} | {row['plate']}",
            value=(
                f"Vehicle: {row['color']} {row['model']}\n"
                f"Description: {row['description']}\n"
                f"Issued by: {row['issuer_name']}\n"
                f"Created: {timestamp(row['created_at'])}"
            ),
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed
    )


@cad_command(
    name="bolo_clear",
    description="Clear an active BOLO.",
)
@app_commands.describe(
    bolo_id="BOLO ID to clear",
)
async def bolo_clear(
    interaction: discord.Interaction,
    bolo_id: int,
):
    cursor = db.execute(
        """
        UPDATE bolos
        SET active = 0
        WHERE id = ?
        AND active = 1
        """,
        (bolo_id,),
    )

    db.commit()

    if cursor.rowcount == 0:
        await interaction.response.send_message(
            "That active BOLO could not be found.",
            ephemeral=True,
        )
        return

    audit(
        interaction.user.id,
        "BOLO_CLEARED",
        str(bolo_id),
    )

    await interaction.response.send_message(
        f"BOLO **#{bolo_id}** has been cleared."
    )


# =====================================================
# FINE SYSTEM
# =====================================================

class FineView(discord.ui.View):
    def __init__(self, fine_id, target_id):
        super().__init__(timeout=604800)

        self.fine_id = fine_id
        self.target_id = target_id

    async def decide(
        self,
        interaction: discord.Interaction,
        accepted: bool,
    ):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "Only the designated recipient can respond to this fine.",
                ephemeral=True,
            )
            return

        row = db.execute(
            "SELECT status FROM fines WHERE id = ?",
            (self.fine_id,),
        ).fetchone()

        if not row or row["status"] != "pending":
            await interaction.response.send_message(
                "This fine has already been resolved.",
                ephemeral=True,
            )
            return

        new_status = (
            "accepted"
            if accepted
            else "declined"
        )

        db.execute(
            """
            UPDATE fines
            SET status = ?, decided_at = ?
            WHERE id = ?
            AND status = 'pending'
            """,
            (
                new_status,
                int(time.time()),
                self.fine_id,
            ),
        )

        db.commit()

        audit(
            interaction.user.id,
            "FINE_DECISION",
            f"Fine #{self.fine_id}: {new_status}",
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Fine #{self.fine_id}: "
                f"**{new_status.upper()}**"
            ),
            view=self,
        )

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.decide(
            interaction,
            True,
        )

    @discord.ui.button(
        label="Decline",
        style=discord.ButtonStyle.danger,
    )
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.decide(
            interaction,
            False,
        )


@cad_command(
    name="fine",
    description="Issue a pending fine.",
)
@app_commands.describe(
    target="Person receiving the fine",
    amount="Fine amount",
    reason="Reason for the fine",
)
async def fine(
    interaction: discord.Interaction,
    target: discord.Member,
    amount: app_commands.Range[int, 1, 1000000],
    reason: str,
):
    channel = bot.get_channel(
        FINE_CONFIRMATION_CHANNEL_ID
    )

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "The fine confirmation channel is unavailable.",
            ephemeral=True,
        )
        return

    cursor = db.execute(
        """
        INSERT INTO fines
        (
            target_id,
            issuer_id,
            amount,
            reason,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (
            target.id,
            interaction.user.id,
            amount,
            reason,
            int(time.time()),
        ),
    )

    db.commit()

    fine_id = cursor.lastrowid

    embed = discord.Embed(
        title=f"Fine #{fine_id} | Confirmation Required",
        color=discord.Color.gold(),
    )

    embed.add_field(
        name="Recipient",
        value=target.mention,
    )

    embed.add_field(
        name="Amount",
        value=f"${amount:,}",
    )

    embed.add_field(
        name="Reason",
        value=reason,
        inline=False,
    )

    embed.add_field(
        name="Issued By",
        value=interaction.user.mention,
    )

    await channel.send(
        content=target.mention,
        embed=embed,
        view=FineView(
            fine_id,
            target.id,
        ),
    )

    audit(
        interaction.user.id,
        "FINE_ISSUED",
        f"Fine #{fine_id} for {target.id}: ${amount}",
    )

    await interaction.response.send_message(
        f"Fine **#{fine_id}** sent to the separate confirmation channel.",
        ephemeral=True,
    )


@cad_command(
    name="fines",
    description="View fine records.",
)
async def fines(interaction: discord.Interaction):
    rows = db.execute(
        """
        SELECT *
        FROM fines
        ORDER BY created_at DESC
        LIMIT 25
        """
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "No fines have been recorded.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Fine Records",
        color=discord.Color.gold(),
    )

    for row in rows:
        embed.add_field(
            name=f"Fine #{row['id']} | ${row['amount']:,}",
            value=(
                f"Recipient: <@{row['target_id']}>\n"
                f"Reason: {row['reason']}\n"
                f"Status: **{row['status'].upper()}**\n"
                f"Created: {timestamp(row['created_at'])}"
            ),
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed
    )


# =====================================================
# WARRANT SYSTEM
# =====================================================

class WarrantRequesterView(discord.ui.View):
    def __init__(
        self,
        warrant_id,
        requester_id,
    ):
        super().__init__(timeout=604800)

        self.warrant_id = warrant_id
        self.requester_id = requester_id

    @discord.ui.button(
        label="Confirm Request",
        style=discord.ButtonStyle.primary,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the original requester can confirm this warrant.",
                ephemeral=True,
            )
            return

        row = db.execute(
            "SELECT * FROM warrants WHERE id = ?",
            (self.warrant_id,),
        ).fetchone()

        if not row or row["status"] != "awaiting_requester":
            await interaction.response.send_message(
                "This request has already been processed.",
                ephemeral=True,
            )
            return

        supervisor_channel = bot.get_channel(
            WARRANT_CONFIRMATION_CHANNEL_ID
        )

        if not isinstance(
            supervisor_channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "Supervisor review channel is unavailable.",
                ephemeral=True,
            )
            return

        db.execute(
            """
            UPDATE warrants
            SET status = 'awaiting_supervisor'
            WHERE id = ?
            """,
            (self.warrant_id,),
        )

        db.commit()

        embed = discord.Embed(
            title=(
                f"Warrant #{self.warrant_id} "
                "| Supervisor Review"
            ),
            color=discord.Color.red(),
        )

        embed.add_field(
            name="Target",
            value=row["target_name"],
        )

        embed.add_field(
            name="Armed",
            value=(
                "Yes"
                if row["armed"]
                else "No"
            ),
        )

        embed.add_field(
            name="Reason",
            value=row["reason"],
            inline=False,
        )

        embed.add_field(
            name="Requested By",
            value=f"<@{row['requester_id']}>",
        )

        await supervisor_channel.send(
            content=f"<@&{SUPERVISOR_ROLE_ID}>",
            embed=embed,
            view=WarrantSupervisorView(
                self.warrant_id
            ),
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Warrant #{self.warrant_id} "
                "submitted for supervisor review."
            ),
            view=self,
        )

    @discord.ui.button(
        label="Cancel Request",
        style=discord.ButtonStyle.danger,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the original requester can cancel this request.",
                ephemeral=True,
            )
            return

        db.execute(
            """
            UPDATE warrants
            SET status = 'cancelled'
            WHERE id = ?
            AND status = 'awaiting_requester'
            """,
            (self.warrant_id,),
        )

        db.commit()

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Warrant #{self.warrant_id} "
                "request cancelled."
            ),
            view=self,
        )


class WarrantSupervisorView(discord.ui.View):
    def __init__(self, warrant_id):
        super().__init__(timeout=604800)

        self.warrant_id = warrant_id

    async def review(
        self,
        interaction: discord.Interaction,
        approved: bool,
    ):
        if not await supervisor_check(interaction):
            await interaction.response.send_message(
                "You are not authorized to review warrants.",
                ephemeral=True,
            )
            return

        row = db.execute(
            "SELECT status FROM warrants WHERE id = ?",
            (self.warrant_id,),
        ).fetchone()

        if not row or row["status"] != "awaiting_supervisor":
            await interaction.response.send_message(
                "This warrant has already been reviewed.",
                ephemeral=True,
            )
            return

        status = (
            "approved"
            if approved
            else "denied"
        )

        db.execute(
            """
            UPDATE warrants
            SET
                status = ?,
                decided_at = ?,
                supervisor_id = ?
            WHERE id = ?
            """,
            (
                status,
                int(time.time()),
                interaction.user.id,
                self.warrant_id,
            ),
        )

        db.commit()

        audit(
            interaction.user.id,
            "WARRANT_REVIEW",
            f"Warrant #{self.warrant_id}: {status}",
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Warrant #{self.warrant_id}: "
                f"**{status.upper()}**"
            ),
            view=self,
        )

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.review(
            interaction,
            True,
        )

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
    )
    async def deny(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.review(
            interaction,
            False,
        )


@cad_command(
    name="warrant",
    description="Submit a warrant request.",
)
@app_commands.describe(
    target_name="Name of the person",
    armed="Whether the subject is believed to be armed",
    reason="Reason for the warrant",
)
@app_commands.choices(
    armed=[
        app_commands.Choice(
            name="Armed",
            value="yes",
        ),
        app_commands.Choice(
            name="Not armed",
            value="no",
        ),
    ]
)
async def warrant(
    interaction: discord.Interaction,
    target_name: str,
    armed: app_commands.Choice[str],
    reason: str,
):
    channel = bot.get_channel(
        WARRANT_CONFIRMATION_CHANNEL_ID
    )

    if not isinstance(
        channel,
        discord.TextChannel,
    ):
        await interaction.response.send_message(
            "The warrant confirmation channel is unavailable.",
            ephemeral=True,
        )
        return

    cursor = db.execute(
        """
        INSERT INTO warrants
        (
            target_name,
            armed,
            reason,
            requester_id,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, 'awaiting_requester', ?)
        """,
        (
            target_name,
            int(armed.value == "yes"),
            reason,
            interaction.user.id,
            int(time.time()),
        ),
    )

    db.commit()

    warrant_id = cursor.lastrowid

    embed = discord.Embed(
        title=(
            f"Warrant #{warrant_id} "
            "| Confirm Your Request"
        ),
        color=discord.Color.red(),
    )

    embed.add_field(
        name="Target",
        value=target_name,
    )

    embed.add_field(
        name="Armed",
        value=armed.name,
    )

    embed.add_field(
        name="Reason",
        value=reason,
        inline=False,
    )

    embed.add_field(
        name="Requester",
        value=interaction.user.mention,
    )

    await channel.send(
        content=interaction.user.mention,
        embed=embed,
        view=WarrantRequesterView(
            warrant_id,
            interaction.user.id,
        ),
    )

    audit(
        interaction.user.id,
        "WARRANT_REQUESTED",
        f"Warrant #{warrant_id}: {target_name}",
    )

    await interaction.response.send_message(
        (
            f"Warrant request **#{warrant_id}** "
            "sent for your confirmation."
        ),
        ephemeral=True,
    )


@cad_command(
    name="warrants",
    description="View warrant records.",
)
async def warrants(interaction: discord.Interaction):
    rows = db.execute(
        """
        SELECT *
        FROM warrants
        ORDER BY created_at DESC
        LIMIT 25
        """
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "No warrant records exist.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Warrant Records",
        color=discord.Color.red(),
    )

    for row in rows:
        embed.add_field(
            name=(
                f"Warrant #{row['id']} "
                f"| {row['target_name']}"
            ),
            value=(
                f"Armed: "
                f"{'Yes' if row['armed'] else 'No'}\n"
                f"Reason: {row['reason']}\n"
                f"Status: **{row['status'].upper()}**\n"
                f"Requester: <@{row['requester_id']}>"
            ),
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed
    )


# =====================================================
# REGISTER SLASH COMMANDS
# =====================================================

for command in (
    dp,
    units,
    staff_add,
    staff_remove,
    staff_list,
    bolo,
    bolos,
    bolo_clear,
    fine,
    fines,
    warrant,
    warrants,
):
    bot.tree.add_command(command)


# =====================================================
# ERROR HANDLING
# =====================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error,
):
    if isinstance(error, app_commands.CheckFailure):
        message = (
            "You are not authorized to use this command."
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )
        except discord.NotFound:
            pass

        return

    print(
        "Slash command error:",
        repr(error),
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                "An internal error occurred.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "An internal error occurred.",
                ephemeral=True,
            )
    except discord.NotFound:
        pass


# =====================================================
# RENDER HEALTH SERVER
# =====================================================

async def health(request):
    return web.Response(
        text="7Rings-law online"
    )


async def start_health_server():
    app = web.Application()

    app.router.add_get(
        "/",
        health,
    )

    app.router.add_get(
        "/health",
        health,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    print(
        f"Health server listening on 0.0.0.0:{port}"
    )

    return runner


# =====================================================
# STARTUP
# =====================================================

@bot.event
async def on_ready():
    print(
        f"CAD online as {bot.user}"
    )

    print(
        f"Department: {DEPARTMENT}"
    )

    print(
        "In-game integration: disabled"
    )

    print(
        "Discord CAD systems loaded."
    )


async def main():
    runner = await start_health_server()

    try:
        async with bot:
            synced = await bot.tree.sync()

            print(
                f"Synced {len(synced)} global slash commands."
            )

            await bot.start(
                DISCORD_TOKEN
            )

    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
