"""The member-facing form: a Start button on each forum's pinned post, then one modal per template page."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import TYPE_CHECKING

import discord
from discord import ui

from .posting import Submission, publish
from .templates import Field

if TYPE_CHECKING:
    from .context import ForumsContext

log = logging.getLogger(__name__)

START_BUTTON_ID = "marketmule:forums:start"
FORM_TIMEOUT = 30 * 60

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def build_select(field: Field, selected: Collection[str] = ()) -> ui.Select:
    return ui.Select(
        placeholder=field.placeholder or field.label,
        min_values=1 if field.required else 0,
        max_values=min(field.max_values, len(field.options)),
        options=[discord.SelectOption(label=o, value=o, default=o in selected) for o in field.options],
        required=field.required,
    )


class StartView(ui.View):
    """Persistent view on a forum's pinned post. One instance handles every forum."""

    def __init__(self, forums: ForumsContext, label: str = "Start", style: str = "primary") -> None:
        super().__init__(timeout=None)
        self.forums = forums
        button: ui.Button = ui.Button(
            label=label,
            style=BUTTON_STYLES.get(style, discord.ButtonStyle.primary),
            custom_id=START_BUTTON_ID,
        )
        button.callback = self.start
        self.add_item(button)

    async def start(self, interaction: discord.Interaction) -> None:
        forum_id = getattr(interaction.channel, "parent_id", None)
        binding = await self.forums.storage.get_binding(forum_id) if forum_id else None
        template = self.forums.templates.get(binding.template_id) if binding else None
        if binding is None or template is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This form isn't available right now. Please let a staff member know.", ephemeral=True
            )
            return
        submission = Submission(template=template, binding=binding, author=interaction.user)
        await interaction.response.send_modal(FormPage(self.forums, submission, 0))


class FormPage(ui.Modal):
    def __init__(self, forums: ForumsContext, submission: Submission, index: int) -> None:
        page = submission.template.pages[index]
        super().__init__(title=page.title, timeout=FORM_TIMEOUT)
        self.forums = forums
        self.submission = submission
        self.index = index
        self.inputs: list[tuple[Field, ui.Item]] = []

        for field in page.fields:
            if field.kind == "note":
                self.add_item(ui.TextDisplay(field.text))
                continue
            component = self._build_input(field)
            self.inputs.append((field, component))
            self.add_item(ui.Label(text=field.label, description=field.description, component=component))

    @property
    def is_last(self) -> bool:
        return self.index == len(self.submission.template.pages) - 1

    def _build_input(self, field: Field) -> ui.Item:
        if field.kind == "file":
            return ui.FileUpload(required=field.required, max_values=field.max_values)
        if field.kind == "select":
            return build_select(field, self.submission.choices.get(field.key, ()))
        return ui.TextInput(
            style=discord.TextStyle.paragraph if field.paragraph else discord.TextStyle.short,
            required=field.required,
            max_length=field.max_length,
            placeholder=field.placeholder,
            default=self.submission.text.get(field.key) or None,
        )

    def _collect(self) -> None:
        for field, component in self.inputs:
            if isinstance(component, ui.FileUpload):
                self.submission.attachments[field.key] = list(component.values)
            elif isinstance(component, ui.Select):
                self.submission.choices[field.key] = list(component.values)
            elif isinstance(component, ui.TextInput):
                self.submission.text[field.key] = component.value.strip()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self._collect()
        template = self.submission.template

        if not self.is_last:
            next_page = template.pages[self.index + 1]
            await self._reply(
                interaction,
                f"**{template.name}**: step {self.index + 1} of {len(template.pages)} done. "
                f"Next: **{next_page.name}**.",
                ContinueView(self.forums, self.submission, self.index + 1),
            )
            return

        await self._reply(interaction, f"**{template.name}**: posting…", None)
        try:
            thread = await publish(self.forums, self.submission)
        except Exception:
            log.exception("Failed to publish %s for %s", template.id, interaction.user)
            await interaction.edit_original_response(
                content="Something went wrong creating your post. Please let a staff member know."
            )
            return
        await interaction.edit_original_response(
            content=f"Posted: {thread.jump_url}\n-# You'll be notified in the post when staff update it."
        )

    async def _reply(self, interaction: discord.Interaction, content: str, view: ui.View | None) -> None:
        # Page 1 opens from the pinned post, so it starts a new private message.
        # Later pages open from that message, so they update it in place.
        if self.index == 0:
            if view is None:
                await interaction.response.send_message(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(content=content, view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        log.exception("Form error", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong with the form.", ephemeral=True)


class ContinueView(ui.View):
    """Discord can't open a modal from another modal's submit, so pages are joined by a button."""

    def __init__(self, forums: ForumsContext, submission: Submission, index: int) -> None:
        super().__init__(timeout=FORM_TIMEOUT)
        self.forums = forums
        self.submission = submission
        self.index = index
        total = len(submission.template.pages)
        button: ui.Button = ui.Button(
            label=f"Continue to step {index + 1} of {total}", style=discord.ButtonStyle.success
        )
        button.callback = self.open_next
        self.add_item(button)

    async def open_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FormPage(self.forums, self.submission, self.index))
