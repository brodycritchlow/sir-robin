from typing import Callable

import discord
from discord.ext import commands
from botcore.utils.logging import get_logger
from botcore.site_api import ResponseCodeError

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.bot import SirRobin

from bot.constants import Channels, Roles

log = get_logger(__name__)


class JamTeamInfoConfirmation(discord.ui.View):

    def __init__(self, bot: 'SirRobin', guild: discord.Guild, original_author: discord.Member):
        super().__init__()
        self.bot = bot
        self.guild = guild
        self.original_author = original_author

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        button.label = "Cancelled"
        button.disabled = True
        self.announce.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label='Announce teams', style=discord.ButtonStyle.green)
    async def announce(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        button.label = "Teams have been announced!"
        button.disabled = True
        self.cancel.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)
        announcements = self.guild.get_channel(Channels.summer_code_jam_announcements)
        log.info(Roles.code_jam_participants)
        await announcements.send(
            f"<@&{Roles.code_jam_participants}> ! You have been sorted into a team!"
            " Click the button below to get a detailed description!",
            view=JamTeamInfoView(self.bot)
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Global check to ensure that the interacting user is the user who invoked the command originally."""
        if interaction.user != self.original_author:
            await interaction.response.send_message(
                ":x: You can't interact with someone else's response. Please run the command yourself!",
                ephemeral=True
            )
            return False
        return True


class JamTeamInfoView(discord.ui.View):

    def __init__(self, bot: 'SirRobin'):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Show me my team!", style=discord.ButtonStyle.blurple, custom_id="CJ:PERS:SHOW_TEAM")
    async def show_team(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        try:
            team = await self.bot.code_jam_mgmt_api.get(f"users/{interaction.user.id}/current_team",
                                                        raise_for_status=True)
        except ResponseCodeError as err:
            if err.response == 404:
                interaction.response.send_message("It seems like you're not a participant!")
            else:
                interaction.response.send_message("Something went wrong!")
                log.error(err.response)
        else:
            response_embed = discord.Embed(
                title=f"You have been sorted into {team['team']['name']}",
                colour=discord.Colour.og_blurple()
            )
            team_channel = f"<#{team['team']['discord_channel_id']}>"
            team_members = [f"<@{member['user_id']}>" for member in team["team"]["users"]]
            response_embed.add_field(name="Your team's channel:", value=team_channel)
            response_embed.add_field(name="Your team's members:", value="\n".join(team_members))
            response_embed.set_footer(text="Good luck!")
            await interaction.response.send_message(
                embed=response_embed,
                ephemeral=True
            )


class JamCreationConfirmation(discord.ui.View):
    def __init__(
            self,
            ctx: commands.Context,
            teams: dict[str: list[dict[str: discord.Member, str: bool]]],
            bot: 'SirRobin', guild: discord.Guild,
            original_author: discord.Member,
            callback: Callable
    ):
        super().__init__()
        self.bot = bot
        self.ctx = ctx
        self.teams = teams
        self.guild = guild
        self.original_author = original_author
        self.callback = callback

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        button.label = "Cancelled"
        button.disabled = True
        self.confirm.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        button.label = "Confirmed"
        button.disabled = True
        self.cancel.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
        await self.callback(self.ctx, self.teams, self.bot)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Global check to ensure that the interacting user is the user who invoked the command originally."""
        if interaction.user != self.original_author:
            await interaction.response.send_message(
                ":x: You can't interact with someone else's response. Please run the command yourself!",
                ephemeral=True
            )
            return False
        return True


class JamEndConfirmation(discord.ui.View):
    def __init__(
            self,
            category_channels: dict[discord.CategoryChannel: list[discord.TextChannel]],
            roles: list[discord.Role],
            callback: Callable,
            author: discord.Member
    ):
        super().__init__()
        self.category_channels = category_channels
        self.roles = roles
        self.original_author = author
        self.callback = callback

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        button.label = "Cancelled"
        button.disabled = True
        self.confirm.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        button.label = "Confirmed"
        button.disabled = True
        self.cancel.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
        await self.callback(self.category_channels, self.roles)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Global check to ensure that the interacting user is the user who invoked the command originally."""
        if interaction.user != self.original_author:
            await interaction.response.send_message(
                ":x: You can't interact with someone else's response. Please run the command yourself!",
                ephemeral=True
            )
            return False
        return True
