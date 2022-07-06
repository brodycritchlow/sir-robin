import csv
import typing as t
from collections import defaultdict
from typing import Optional
from urllib.parse import quote as quote_url

import discord
from botcore.site_api import APIClient, ResponseCodeError
from botcore.utils.logging import get_logger
from botcore.utils.members import get_or_fetch_member
from discord import Colour, Embed, Guild, Member
from discord.ext import commands

from bot.bot import SirRobin
from bot.constants import Roles
from bot.exts.code_jams import _creation_utils
from bot.exts.code_jams._flows import (TEAM_LEADER_ROLE_NAME, creation_flow,
                                       deletion_flow)
from bot.exts.code_jams._views import (JamCreationConfirmation,
                                       JamEndConfirmation,
                                       JamTeamInfoConfirmation)
from bot.services import send_to_paste_service

log = get_logger(__name__)


class CodeJams(commands.Cog):
    """Manages the code-jam related parts of our server."""

    def __init__(self, bot: SirRobin):
        self.bot = bot

    @commands.group(aliases=("cj", "jam"))
    @commands.has_any_role(Roles.admins)
    async def codejam(self, ctx: commands.Context) -> None:
        """A Group of commands for managing Code Jams."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @codejam.command()
    async def create(self, ctx: commands.Context, csv_file: t.Optional[str] = None) -> None:
        """
        Create code-jam teams from a CSV file or a link to one, specifying the team names, leaders and members.

        The CSV file must have 3 columns: 'Team Name', 'Team Member Discord ID', and 'Team Leader'.

        This will create the text channels for the teams, and give the team leaders their roles.
        """
        async with ctx.typing():
            if csv_file:
                async with self.bot.http_session.get(csv_file) as response:
                    if response.status != 200:
                        await ctx.send(f"Got a bad response from the URL: {response.status}")
                        return

                    csv_file = await response.text()

            elif ctx.message.attachments:
                csv_file = (await ctx.message.attachments[0].read()).decode("utf8")
            else:
                raise commands.BadArgument("You must include either a CSV file or a link to one.")

            teams = defaultdict(list)
            reader = csv.DictReader(csv_file.splitlines())

            for row in reader:
                member = await get_or_fetch_member(ctx.guild, int(row["Team Member Discord ID"]))

                if member is None:
                    log.trace(f"Got an invalid member ID: {row['Team Member Discord ID']}")
                    continue

                teams[row["Team Name"]].append({"member": member, "is_leader": row["Team Leader"].upper() == "Y"})
            warning_embed = Embed(
                colour=discord.Colour.orange(),
                title="Warning!",
                description=f"{len(teams)} teams, and roles will be created, are you sure?"
            )
            warning_embed.set_footer(text="Code Jam team generation")

            await ctx.send(
                embed=warning_embed,
                view=JamCreationConfirmation(ctx, teams, self.bot, ctx.guild, ctx.author, creation_flow)
            )

    @codejam.command()
    @commands.has_any_role(Roles.admins)
    async def announce(self, ctx: commands.Context) -> None:
        """A command to send an announcement embed to the CJ announcement channel."""
        team_info_view = JamTeamInfoConfirmation(self.bot, ctx.guild, ctx.author)
        embed_conf = Embed(title="Would you like to announce the teams?", colour=discord.Colour.og_blurple())
        await ctx.send(
            embed=embed_conf,
            view=team_info_view
        )

    @codejam.command()
    @commands.has_any_role(Roles.admins)
    async def end(self, ctx: commands.Context) -> None:
        """
        Delete all code jam channels.

        A confirmation message is displayed with the categories and channels
        that are going to be deleted, by pressing "Confirm" the deletion
        process will begin.
        """
        categories = self.jam_categories(ctx.guild)
        roles = await self.jam_roles(ctx.guild, self.bot.code_jam_mgmt_api)
        if not categories and not roles:
            await ctx.send(":x: The Code Jam channels and roles have already been deleted! ")
            return

        category_channels: dict[discord.CategoryChannel: list[discord.TextChannel]] = {
            category: category.channels.copy() for category in categories
        }

        details = "Categories and Channels: \n"
        for category, channels in category_channels.items():
            details += f"{category.name}[{category.id}]: {','.join([channel.name for channel in channels])}\n"
        details += "Roles:\n"
        for role in roles:
            details += f"{role.name}[{role.id}]\n"
        url = await send_to_paste_service(details)
        if not url:
            url = "**Unable to send deletion details to the pasting service.**"
        warning_embed = Embed(title="Are you sure?", colour=discord.Colour.orange())
        warning_embed.add_field(
            name="For a detailed list of which roles, categories and channels will be deleted see:",
            value=url
        )
        confirm_view = JamEndConfirmation(category_channels, roles, deletion_flow, ctx.author)
        await ctx.send(
            embed=warning_embed,
            view=confirm_view
        )
        await confirm_view.wait()
        await ctx.send("Code Jam has officially ended! :sunrise:")

    @codejam.command()
    @commands.has_any_role(Roles.admins, Roles.code_jam_event_team)
    async def info(self, ctx: commands.Context, member: Member) -> None:
        """
        Send an info embed about the member with the team they're in.

        The team is found by issuing a request to the CJ Management System
        """
        try:
            team = await self.bot.code_jam_mgmt_api.get(f"users/{member.id}/current_team",
                                                        raise_for_status=True)
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(":x: It seems like the user is not a participant!")
            else:
                await ctx.send("Something went wrong while processing the request! We have notified the team!")
                log.error(f"Something went wrong with processing the request! {err}")
        else:
            embed = Embed(
                title=str(member),
                colour=Colour.og_blurple()
            )
            embed.add_field(name="Team", value=team["team"]["name"], inline=True)

            await ctx.send(embed=embed)

    @codejam.command()
    @commands.has_any_role(Roles.admins)
    async def move(self, ctx: commands.Context, member: Member, *, new_team_name: str) -> None:
        """Move participant from one team to another by issuing an HTTP request to the Code Jam Management system."""
        # Query the team the user has to be moved to
        try:
            team_to_move_in = await self.bot.code_jam_mgmt_api.get("teams/find", params={"name": new_team_name},
                                                                   raise_for_status=True)
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(f":x: Team `{new_team_name}` does not exists in the database!")
            else:
                await ctx.send("Something went wrong while processing the request! We have notified the team!")
                log.error(f"Something went wrong with processing the request! {err}")
            return

        # Query the current team of the member
        try:
            team = await self.bot.code_jam_mgmt_api.get(f"users/{member.id}/current_team",
                                                        raise_for_status=True)
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(":x: It seems like the user is not a participant!")
            else:
                await ctx.send("Something went wrong while processing the request! We have notified the team!")
                log.error(err.response)
            return
        # Remove the member from their current team.
        try:
            await self.bot.code_jam_mgmt_api.delete(
                f"teams/{quote_url(str(team['team']['id']))}/users/{quote_url(str(team['user_id']))}",
                raise_for_status=True
            )
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(":x: Team or user could not be found!")
            elif err.response.status == 400:
                await ctx.send(":x: The member given is not part of the team! (Might have been removed already)")
            else:
                await ctx.send("Something went wrong while processing the request! We have notified the team!")
                log.error(f"Something went wrong with processing the request! {err}")
            return

        # Actually remove the role to modify the permissions.
        team_role = ctx.guild.get_role(team["team"]["discord_role_id"])
        await member.remove_roles(team_role)

        # Decide whether the member should be a team leader in their new team.
        is_leader = False
        members = team["team"]["users"]
        for memb in members:
            if memb["user_id"] == member.id and memb["is_leader"]:
                is_leader = True

        # Add the user to the new team in the database.
        try:
            await self.bot.code_jam_mgmt_api.post(
                f"teams/{team_to_move_in['id']}/users/{member.id}",
                params={"is_leader": str(is_leader)},
                raise_for_status=True
            )
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(":x: Team or user could not be found.")
                log.info(err)
            elif err.response.status == 400:
                await ctx.send(f":x: user {member.mention} is already in {team_to_move_in['team']['name']}")
            else:
                await ctx.send(
                    "Something went wrong while processing the request! We have notified the team!"
                )
                log.error(f"Something went wrong with processing the request! {err}")
            return

        await member.add_roles(ctx.guild.get_role(team_to_move_in['discord_role_id']))

        await ctx.send(
            f"Success! Participant {member.mention} has been moved "
            f"from {team['team']['name']} to {team_to_move_in['name']}"
        )

    @codejam.command()
    @commands.has_any_role(Roles.admins)
    async def remove(self, ctx: commands.Context, member: Member) -> None:
        """Remove the participant from their team. Does not remove the participants or leader roles."""
        try:
            team = await self.bot.code_jam_mgmt_api.get(f"users/{member.id}/current_team",
                                                        raise_for_status=True)
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(":x: It seems like the user is not a participant!")
            else:
                await ctx.send("Something went wrong while processing the request! We have notified the team!")
                log.error(err.response)
            return

        try:
            await self.bot.code_jam_mgmt_api.delete(
                f"teams/{quote_url(str(team['team']['id']))}/users/{quote_url(str(team['user_id']))}",
                raise_for_status=True
            )
        except ResponseCodeError as err:
            if err.response.status == 404:
                await ctx.send(":x: Team or user could not be found!")
            elif err.response.status == 400:
                await ctx.send(":x: The member given is not part of the team! (Might have been removed already)")
            else:
                await ctx.send("Something went wrong while processing the request! We have notified the team!")
                log.error(err.response)
            return

        team_role = ctx.guild.get_role(team["team"]["discord_role_id"])
        await member.remove_roles(team_role)
        for role in member.roles:
            if role.name == TEAM_LEADER_ROLE_NAME:
                await member.remove_roles(role)
        await ctx.send(f"Successfully removed {member.mention} from team {team['team']['name']}")

    @staticmethod
    def jam_categories(guild: Guild) -> list[discord.CategoryChannel]:
        """Get all the code jam team categories."""
        return [category for category in guild.categories if category.name == _creation_utils.CATEGORY_NAME]

    @staticmethod
    async def jam_roles(guild: Guild, mgmt_client: APIClient) -> Optional[list[discord.Role]]:
        """Get all the code jam team roles."""
        try:
            roles_raw = await mgmt_client.get("teams", raise_for_status=True, params={"current_jam": "true"})
        except ResponseCodeError:
            log.error("Could not fetch Roles from the Code Jam Management API")
            return
        else:
            roles = []
            for role in roles_raw:
                if role := guild.get_role(role["discord_role_id"]):
                    roles.append(role)
            return roles

    @staticmethod
    def team_channel(guild: Guild, criterion: t.Union[str, Member]) -> t.Optional[discord.TextChannel]:
        """Get a team channel through either a participant or the team name."""
        for category in CodeJams.jam_categories(guild):
            for channel in category.channels:
                if isinstance(channel, discord.TextChannel):
                    if (
                            # If it's a string.
                            criterion == channel.name or criterion == CodeJams.team_name(channel)
                            # If it's a member.
                            or criterion in channel.overwrites
                    ):
                        return channel

    @staticmethod
    def team_name(channel: discord.TextChannel) -> str:
        """Retrieves the team name from the given channel."""
        return channel.name.replace("-", " ").title()
