"""Stall buttons: Get the template (Start here post); Bump, Add picture, Remove picture and Close stall
(under each stall); Reopen stall.

They're persistent: one copy registered at startup keeps the buttons on every post working after restarts.
Only a stall's owner (and staff) can use its buttons.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

from marketmule.core.staff import is_staff

from . import service
from .render import Picture, control_text, picture_caption, template_message
from .service import StallError
from .storage import Stall, now

if TYPE_CHECKING:
    from .context import StallContext

log = logging.getLogger(__name__)

TEMPLATE_BUTTON_ID = "marketmule:stalls:template"
BUMP_BUTTON_ID = "marketmule:stalls:bump"
CLOSE_BUTTON_ID = "marketmule:stalls:close"
ADD_PICTURE_BUTTON_ID = "marketmule:stalls:addpicture"
REMOVE_PICTURE_BUTTON_ID = "marketmule:stalls:removepicture"
OLD_PICTURES_BUTTON_ID = "marketmule:stalls:pictures"  # on control messages from before picture rows
MAX_DESCRIPTION = 150
FORM_TIMEOUT = 15 * 60
REOPEN_BUTTON_ID = "marketmule:stalls:reopen"
CONFIRM_TIMEOUT = 5 * 60
NOT_OWNER = "Only the stall's owner can use these buttons. Want your own stall? See the Start here post."
STAFF_CLOSED = "Staff closed this stall, so only staff can reopen it. Please ask a staff member."


def _emoji(ctx: StallContext | None, icon: str, fallback: str) -> discord.Emoji | str:
    icons = getattr(getattr(ctx, "bot", None), "icons", None)
    return (icons.emoji(icon, fallback) if icons else fallback) or fallback


async def _say(interaction: discord.Interaction, content: str, view: ui.View | None = None) -> None:
    """Reply privately, whether or not the interaction was already answered."""
    kwargs: dict[str, Any] = {"view": view} if view is not None else {}
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=True, **kwargs)


async def _guarded(interaction: discord.Interaction, action: Callable[[], Awaitable[None]]) -> None:
    """Run a button's action, turning errors into a private message instead of "This interaction failed"."""
    try:
        await action()
    except StallError as exc:
        await _say(interaction, str(exc))
    except Exception:
        log.exception("Stall button failed")
        await _say(interaction, "Something went wrong. Please try again, or let a staff member know.")


async def can_manage(ctx: StallContext, user: discord.abc.User, stall: Stall) -> bool:
    return user.id == stall.owner_id or await is_staff(ctx.bot.db, user)


async def _stall_here(ctx: StallContext, interaction: discord.Interaction) -> Stall | None:
    return await ctx.storage.get_stall(interaction.channel_id or 0)


class StallPanel(ui.LayoutView):
    """The pinned Start here post: how stalls work, and a button that sends the template privately."""

    def __init__(self, ctx: StallContext, banner: str | None = None) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        settings = ctx.settings
        icons = getattr(ctx.bot, "icons", None)
        card = ui.Container(accent_color=settings.color)
        if banner:
            card.add_item(ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{banner}")))
        text = f"## {settings.panel.title}\n{settings.panel.text}"
        card.add_item(ui.TextDisplay(icons.apply(text) if icons else text))
        button: ui.Button = ui.Button(
            label=settings.panel.button_label,
            emoji=_emoji(ctx, settings.icon or "mm_stalls", "🏪"),
            style=discord.ButtonStyle.success,
            custom_id=TEMPLATE_BUTTON_ID,
        )
        button.callback = self.send_template
        card.add_item(ui.ActionRow(button))
        self.add_item(card)

    async def send_template(self, interaction: discord.Interaction) -> None:
        await _say(interaction, template_message(self.ctx.settings))


def _card(ctx: StallContext, text: str, pictures: Sequence[Picture], buttons: Sequence[ui.Button]) -> ui.Container:
    """The control message: the stall line, one row per picture (description beside it), then the buttons."""
    card: ui.Container = ui.Container(accent_color=ctx.settings.color)
    card.add_item(ui.TextDisplay(text))
    for number, picture in enumerate(pictures, start=1):
        caption = picture_caption(ctx.settings, number, picture.description)
        thumbnail = ui.Thumbnail(f"attachment://{picture.filename}", description=picture.description[:256] or None)
        card.add_item(ui.Section(ui.TextDisplay(caption), accessory=thumbnail))
    card.add_item(ui.ActionRow(*buttons))
    return card


async def _open_stall_for(ctx: StallContext, interaction: discord.Interaction) -> Stall | None:
    """The stall in this channel, if it's open and the person pressing is its owner (or staff)."""
    stall = await _stall_here(ctx, interaction)
    if stall is None:
        await _say(interaction, "This stall isn't set up anymore.")
        return None
    if not await can_manage(ctx, interaction.user, stall):
        await _say(interaction, NOT_OWNER)
        return None
    if stall.is_closed:
        await _say(interaction, "This stall is closed.")
        return None
    return stall


async def _exempt(ctx: StallContext, interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and await service.is_exempt(ctx, interaction.guild, interaction.user.id)


class ControlView(ui.LayoutView):
    """Under each open stall: who owns it, its pictures, and Bump / Add picture / Remove picture / Close stall.

    Built without a stall, it's the copy registered at startup that keeps the buttons working."""

    def __init__(self, ctx: StallContext, stall: Stall | None = None, pictures: Sequence[Picture] = ()) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        control = ctx.settings.control
        bump: ui.Button = ui.Button(
            label=control.bump_button,
            emoji=_emoji(ctx, "mm_bump", "🔼"),
            style=discord.ButtonStyle.primary,
            custom_id=BUMP_BUTTON_ID,
        )
        bump.callback = self.bump
        add: ui.Button = ui.Button(
            label=control.add_picture_button,
            emoji=_emoji(ctx, "mm_additem", "🖼️"),
            style=discord.ButtonStyle.secondary,
            custom_id=ADD_PICTURE_BUTTON_ID,
        )
        add.callback = self.add_picture
        remove: ui.Button = ui.Button(
            label=control.remove_picture_button,
            emoji=_emoji(ctx, "mm_removeitem", "🗑️"),
            style=discord.ButtonStyle.secondary,
            custom_id=REMOVE_PICTURE_BUTTON_ID,
        )
        remove.callback = self.remove_picture
        close: ui.Button = ui.Button(
            label=control.close_button,
            emoji=_emoji(ctx, "mm_closestall", "⛔"),
            style=discord.ButtonStyle.secondary,
            custom_id=CLOSE_BUTTON_ID,
        )
        close.callback = self.close
        buttons = [bump, add, remove, close]
        if stall is not None:  # only show the picture buttons that can do something
            if len(pictures) >= ctx.settings.limits.max_pictures:
                buttons.remove(add)
            if not pictures:
                buttons.remove(remove)
        text = control_text(ctx.settings, stall) if stall is not None else control.bump_button
        self.add_item(_card(ctx, text, pictures, buttons))

    async def bump(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await _open_stall_for(self.ctx, interaction)
            if stall is None:
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            by_owner = interaction.user.id == stall.owner_id
            hours = 0 if await _exempt(self.ctx, interaction) else self.ctx.settings.limits.manual_bump_hours
            wait = await service.bump(self.ctx, stall, hours=hours, by_owner=by_owner)
            if wait:
                await _say(interaction, f"You can bump again <t:{now() + wait}:R>.")
            else:
                await _say(interaction, "Bumped! Your stall is at the top of the channel.")

        await _guarded(interaction, act)

    async def add_picture(self, interaction: discord.Interaction) -> None:
        await _open_picture_form(self.ctx, interaction)

    async def remove_picture(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await _open_stall_for(self.ctx, interaction)
            if stall is None:
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            pictures = await service.stall_pictures(self.ctx, stall)
            if not pictures:
                await _say(interaction, "Your stall has no pictures.")
                return
            view = RemovePictures(self.ctx, stall.thread_id, pictures)
            await _say(interaction, self.ctx.settings.control.remove_prompt, view)

        await _guarded(interaction, act)

    async def close(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await _open_stall_for(self.ctx, interaction)
            if stall is not None:
                await _say(
                    interaction,
                    "**Close this stall?** Your post is kept, and you can reopen it any time.",
                    ConfirmClose(self.ctx, stall.thread_id),
                )

        await _guarded(interaction, act)


async def _open_picture_form(ctx: StallContext, interaction: discord.Interaction) -> None:
    async def act() -> None:
        stall = await _open_stall_for(ctx, interaction)
        if stall is not None:
            await interaction.response.send_modal(AddPictureForm(ctx, stall.thread_id))

    await _guarded(interaction, act)


class OldPicturesButton(ui.View):
    """The Pictures button on control messages from before picture rows: it opens the new Add picture form.

    Adding a picture (or the next bump) switches that stall to the new layout."""

    def __init__(self, ctx: StallContext) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        button: ui.Button = ui.Button(label="Pictures", custom_id=OLD_PICTURES_BUTTON_ID)
        button.callback = self.pictures
        self.add_item(button)

    async def pictures(self, interaction: discord.Interaction) -> None:
        await _open_picture_form(self.ctx, interaction)


class AddPictureForm(ui.Modal):
    """One picture and its description. It becomes a row under the stall."""

    def __init__(self, ctx: StallContext, thread_id: int) -> None:
        control = ctx.settings.control
        super().__init__(title=control.picture_form_title, timeout=FORM_TIMEOUT)
        self.ctx = ctx
        self.thread_id = thread_id
        self.upload = ui.FileUpload(required=True, min_values=1, max_values=1)
        self.add_item(ui.Label(text=control.picture_label, description=control.picture_hint, component=self.upload))
        self.description: ui.TextInput = ui.TextInput(
            style=discord.TextStyle.short,
            required=False,
            max_length=MAX_DESCRIPTION,
            placeholder=control.description_placeholder,
        )
        self.add_item(ui.Label(text=control.description_label, component=self.description))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await self.ctx.storage.get_stall(self.thread_id)
            if stall is None or stall.is_closed or stall.is_deleted:
                raise StallError("This stall is closed.")
            if not await can_manage(self.ctx, interaction.user, stall):
                raise StallError(NOT_OWNER)
            if not self.upload.values:
                raise StallError("Please choose a picture to upload.")
            await interaction.response.defer(ephemeral=True, thinking=True)
            exempt = await _exempt(self.ctx, interaction)
            count = await service.add_picture(
                self.ctx, stall, self.upload.values[0], self.description.value or "", exempt=exempt
            )
            await _say(interaction, f"Added! Your stall shows {count} picture(s).")

        await _guarded(interaction, act)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        log.exception("Stall picture form failed", exc_info=error)
        await _say(interaction, "Something went wrong with the upload. Please try again.")


class RemovePictures(ui.View):
    """A private list of the stall's pictures: pick one or more to take off."""

    def __init__(self, ctx: StallContext, thread_id: int, pictures: Sequence[Picture]) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT)
        self.ctx = ctx
        self.thread_id = thread_id
        options = [
            discord.SelectOption(
                label=picture_caption(ctx.settings, number, picture.description).replace("*", "")[:100],
                value=picture.filename,
            )
            for number, picture in enumerate(pictures, start=1)
        ]
        self.choice: ui.Select = ui.Select(
            placeholder="Pictures to remove", min_values=1, max_values=len(options), options=options
        )
        self.choice.callback = self.remove
        self.add_item(self.choice)

    async def remove(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await self.ctx.storage.get_stall(self.thread_id)
            if stall is None or stall.is_closed or stall.is_deleted:
                raise StallError("This stall is closed.")
            if not await can_manage(self.ctx, interaction.user, stall):
                raise StallError(NOT_OWNER)
            await interaction.response.edit_message(content="Removing…", view=None)
            exempt = await _exempt(self.ctx, interaction)
            left = await service.remove_pictures(self.ctx, stall, list(self.choice.values), exempt=exempt)
            await interaction.edit_original_response(content=f"Removed. Your stall shows {left} picture(s).")

        await _guarded(interaction, act)


class ConfirmClose(ui.View):
    def __init__(self, ctx: StallContext, thread_id: int) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT)
        self.ctx = ctx
        self.thread_id = thread_id
        yes: ui.Button = ui.Button(label="Close stall", style=discord.ButtonStyle.danger)
        yes.callback = self.confirm
        no: ui.Button = ui.Button(label="Keep it open", style=discord.ButtonStyle.secondary)
        no.callback = self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def confirm(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await self.ctx.storage.get_stall(self.thread_id)
            if stall is None or stall.is_closed:
                raise StallError("This stall is already closed.")
            by_staff = interaction.user.id != stall.owner_id  # staff closing someone else's stall
            await interaction.response.edit_message(content="Closing the stall…", view=None)
            reason = "Closed by staff." if by_staff else "The owner closed it and can reopen it any time."
            await service.close_stall(self.ctx, stall, reason, by_staff=by_staff)
            done = "Closed. Only staff can reopen it." if by_staff else "Your stall is closed. Reopen it any time."
            await interaction.edit_original_response(content=done)

        await _guarded(interaction, act)

    async def cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Your stall stays open.", view=None)


class ReopenView(ui.LayoutView):
    """Shown on a closed stall: the closed note, its pictures, and Reopen stall.

    Built without text, it's the copy registered at startup that keeps the button working."""

    def __init__(self, ctx: StallContext, text: str | None = None, pictures: Sequence[Picture] = ()) -> None:
        super().__init__(timeout=None)
        self.ctx = ctx
        button: ui.Button = ui.Button(
            label=ctx.settings.control.reopen_button,
            emoji=_emoji(ctx, "mm_stalls", "🏪"),
            style=discord.ButtonStyle.success,
            custom_id=REOPEN_BUTTON_ID,
        )
        button.callback = self.reopen
        self.add_item(_card(ctx, text or ctx.settings.control.reopen_button, pictures, [button]))

    async def reopen(self, interaction: discord.Interaction) -> None:
        async def act() -> None:
            stall = await _stall_here(self.ctx, interaction)
            if stall is None or not await can_manage(self.ctx, interaction.user, stall):
                await _say(interaction, "Only the stall's owner can reopen it.")
                return
            if not stall.is_closed:
                await _say(interaction, "This stall is already open.")
                return
            if stall.closed_by_staff and not await is_staff(self.ctx.bot.db, interaction.user):
                await _say(interaction, STAFF_CLOSED)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            thread = await service.reopen_stall(self.ctx, stall, exempt=await _exempt(self.ctx, interaction))
            await _say(interaction, f"Your stall is open again: {thread.jump_url}")

        await _guarded(interaction, act)
