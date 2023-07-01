import discord
from discord.ext import commands

from core.models import PermissionLevel
from typing import Union

EVENT_STAFF = 1124436859678884000  # Event Staff Role
PERMISSION_LEVEL = PermissionLevel.SUPPORTER  # Alternate Permission Level


def role_or_perm(role: int, perm: PermissionLevel):
    """
    Decorator to check for either a role OR a PermissionLevel.
    Because apparently commands.check_any causes the default PermissionLevel check to break.

    As MM doesn't support local modules, copy this around as needed (I hate this too)
    """
    async def predicate(ctx):
        if await ctx.bot.is_owner(ctx.author) or ctx.author.id == ctx.bot.user.id:
            # Bot owner(s) (and creator) has absolute power over the bot
            return True

        if ctx.author.get_role(role):
            return True

        if (
                perm is not PermissionLevel.OWNER
                and ctx.channel.permissions_for(ctx.author).administrator
                and ctx.guild == ctx.bot.modmail_guild
        ):
            # Administrators have permission to all non-owner commands in the Modmail Guild
            return True

        checkables = {*ctx.author.roles, ctx.author}
        level_permissions = ctx.bot.config["level_permissions"]

        for level in PermissionLevel:
            if level >= perm and level.name in level_permissions:
                # -1 is for @everyone
                if -1 in level_permissions[level.name] or any(
                        str(check.id) in level_permissions[level.name] for check in checkables
                ):
                    return True
        return False

    return commands.check(predicate)


def event_only(func: callable):
    """Decorator for button functions to check for event staff, equivalent, or higher permissions."""

    async def wrapper(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user if isinstance(interaction.user, discord.Member) else interaction.guild.get_member(
            interaction.user.id)
        has_perms = member.get_role(EVENT_STAFF) or await self.bot.is_owner(member) or member.id == self.bot.user.id or (
                PERMISSION_LEVEL is not PermissionLevel.OWNER and
                interaction.channel.permissions_for(member).administrator and
                interaction.guild == self.bot.modmail_guild
        )
        if not has_perms:
            checkables = {*member.roles, member}
            level_permissions = self.bot.config["level_permissions"]

            for level in PermissionLevel:
                if level >= PERMISSION_LEVEL and level.name in level_permissions:
                    # -1 is for @everyone
                    if -1 in level_permissions[level.name] or any(
                            str(check.id) in level_permissions[level.name] for check in checkables
                    ):
                        has_perms = True
                        break

        if has_perms:
            return await func(self, interaction, button)
        else:
            return await interaction.response.send_message(content="You do not have permissions to use this.",
                                                           ephemeral=True)

    return wrapper


class KaraokeQueueView(discord.ui.View):
    def __init__(self, bot: commands.Bot, timeout: int, message: discord.Message):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.message = message
        self.current: Union[discord.Member, None] = None
        self.q_priority = set()
        self.q_normal = set()
        self.has_queued = set()

    async def generate_queue(self):
        embed = discord.Embed(
            title=':microphone: Karaoke',
            description=f"Current Singer 🎙️: {self.current.mention}" if self.current else "No one is currently singing",
            colour=discord.Colour.blue()
        )

        embed.add_field(name="Priority Queue", value="\n".join([f"<@{i}>" for i in self.q_priority]))
        embed.add_field(name="Normal Queue", value="\n".join([f"<@{i}>" for i in self.q_normal]))

        return embed

    async def on_timeout(self):
        self.stop()
        await self.message.edit(view=None)

    # JOIN
    @discord.ui.button(label='Join', style=discord.ButtonStyle.blurple, emoji="👋")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Allows a member to join the queue."""
        if interaction.user.id in self.q_priority or interaction.user.id in self.q_normal:
            return await interaction.response.send_message(content="You're already in the queue!", ephemeral=True)
        elif interaction.user.id in self.has_queued:
            self.q_normal.add(interaction.user.id)
        else:
            self.q_priority.add(interaction.user.id)
            self.has_queued.add(interaction.user.id)

        await interaction.response.send_message(content="You've been added to the queue!", ephemeral=True)
        await self.message.edit(embed=await self.generate_queue())

    # LEAVE
    @discord.ui.button(label='Leave', style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Allows a member to leave the queue."""
        if interaction.user.id in self.q_priority:
            self.q_priority.remove(interaction.user.id)
        elif interaction.user.id in self.q_normal:
            self.q_normal.remove(interaction.user.id)
        else:
            return await interaction.response.send_message(content="You're not in the queue!", ephemeral=True)

        await interaction.response.send_message(content="You've been removed from the queue!", ephemeral=True)
        await self.message.edit(embed=await self.generate_queue())

    # NEXT - STAFF ONLY
    @discord.ui.button(label='Next', style=discord.ButtonStyle.success, emoji="⏭️")
    @event_only
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Moves to the next person in the queue."""
        if len(self.q_priority) > 0:
            new = self.q_priority.pop()
        elif len(self.q_normal) > 0:
            new = self.q_normal.pop()
        else:
            return await interaction.response.send_message(content="There's no one in the queue!", ephemeral=True)

        self.current = interaction.guild.get_member(new)
        await interaction.channel.send(embed=discord.Embed(description=f"{self.current.mention} is now up!", colour=discord.Colour.random()))
        await interaction.response.edit_message(embed=await self.generate_queue())

    @discord.ui.button(label='Reset', style=discord.ButtonStyle.grey, emoji="🗑️")
    @event_only
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reset button, clears the queue."""
        self.stop()
        await self.message.edit(embed=discord.Embed(description="No longer queueing, see you next time!", colour=discord.Colour.red()),
                                view=None)
        await interaction.response.defer()


class Karaoke(commands.Cog):
    """Karaoke Queueing System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(aliases=['karaokeq', 'kq'])
    @role_or_perm(role=EVENT_STAFF, perm=PERMISSION_LEVEL)
    async def karaokequeue(self, ctx: commands.Context, timeout: int = 172800):
        """Starts a karaoke queue in the current channel. Timeout is in seconds. Default is 48 hours."""
        message = await ctx.send("Generating queue...")
        view = KaraokeQueueView(self.bot, timeout, message)
        await message.edit(content="", view=view, embed=await view.generate_queue())


async def setup(bot):
    await bot.add_cog(Karaoke(bot))