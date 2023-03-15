"""Filesystem musicprovider support for MusicAssistant."""
from __future__ import annotations

import asyncio
import os
import os.path
from collections.abc import AsyncGenerator

import aiofiles
from aiofiles.os import wrap

from music_assistant.common.models.errors import SetupFailedError
from music_assistant.constants import CONF_PATH

from .base import FileSystemItem, FileSystemProviderBase
from .helpers import get_absolute_path, get_relative_path

listdir = wrap(os.listdir)
isdir = wrap(os.path.isdir)
isfile = wrap(os.path.isfile)
exists = wrap(os.path.exists)


async def create_item(base_path: str, entry: os.DirEntry) -> FileSystemItem:
    """Create FileSystemItem from os.DirEntry."""

    def _create_item():
        absolute_path = get_absolute_path(base_path, entry.path)
        stat = entry.stat(follow_symlinks=False)
        return FileSystemItem(
            name=entry.name,
            path=get_relative_path(base_path, entry.path),
            absolute_path=absolute_path,
            is_file=entry.is_file(follow_symlinks=False),
            is_dir=entry.is_dir(follow_symlinks=False),
            checksum=str(int(stat.st_mtime)),
            file_size=stat.st_size,
            # local filesystem is always local resolvable
            local_path=absolute_path,
        )

    # run in thread because strictly taken this may be blocking IO
    return await asyncio.to_thread(_create_item)


class LocalFileSystemProvider(FileSystemProviderBase):
    """Implementation of a musicprovider for local files."""

    async def setup(self) -> None:
        """Handle async initialization of the provider."""
        conf_path = self.config.get_value(CONF_PATH)
        if not await isdir(conf_path):
            raise SetupFailedError(f"Music Directory {conf_path} does not exist")

    async def listdir(
        self, path: str, recursive: bool = False
    ) -> AsyncGenerator[FileSystemItem, None]:
        """List contents of a given provider directory/path.

        Parameters
        ----------
        - path: path of the directory (relative or absolute) to list contents of.
            Empty string for provider's root.
        - recursive: If True will recursively keep unwrapping subdirectories (scandir equivalent).

        Returns:
        -------
            AsyncGenerator yielding FileSystemItem objects.

        """
        abs_path = get_absolute_path(self.config.get_value(CONF_PATH), path)
        for entry in await asyncio.to_thread(os.scandir, abs_path):
            if entry.name.startswith("."):
                # skip invalid/system files and dirs
                continue
            item = await create_item(self.config.get_value(CONF_PATH), entry)
            if recursive and item.is_dir:
                try:
                    async for subitem in self.listdir(item.absolute_path, True):
                        yield subitem
                except (OSError, PermissionError) as err:
                    self.logger.warning("Skip folder %s: %s", item.path, str(err))
            else:
                yield item

    async def resolve(
        self, file_path: str, require_local: bool = False  # noqa: ARG002
    ) -> FileSystemItem:
        """Resolve (absolute or relative) path to FileSystemItem.

        If require_local is True, we prefer to have the `local_path` attribute filled
        (e.g. with a tempfile), if supported by the provider/item.
        """
        absolute_path = get_absolute_path(self.config.get_value(CONF_PATH), file_path)

        def _create_item():
            stat = os.stat(absolute_path, follow_symlinks=False)
            return FileSystemItem(
                name=os.path.basename(file_path),
                path=get_relative_path(self.config.get_value(CONF_PATH), file_path),
                absolute_path=absolute_path,
                is_dir=os.path.isdir(absolute_path),
                is_file=os.path.isfile(absolute_path),
                checksum=str(int(stat.st_mtime)),
                file_size=stat.st_size,
                # local filesystem is always local resolvable
                local_path=absolute_path,
            )

        # run in thread because strictly taken this may be blocking IO
        return await asyncio.to_thread(_create_item)

    async def exists(self, file_path: str) -> bool:
        """Return bool is this FileSystem musicprovider has given file/dir."""
        if not file_path:
            return False  # guard
        abs_path = get_absolute_path(self.config.get_value(CONF_PATH), file_path)
        return await exists(abs_path)

    async def read_file_content(self, file_path: str, seek: int = 0) -> AsyncGenerator[bytes, None]:
        """Yield (binary) contents of file in chunks of bytes."""
        abs_path = get_absolute_path(self.config.get_value(CONF_PATH), file_path)
        chunk_size = 512000
        async with aiofiles.open(abs_path, "rb") as _file:
            if seek:
                await _file.seek(seek)
            # yield chunks of data from file
            while True:
                data = await _file.read(chunk_size)
                if not data:
                    break
                yield data

    async def write_file_content(self, file_path: str, data: bytes) -> None:
        """Write entire file content as bytes (e.g. for playlists)."""
        abs_path = get_absolute_path(self.config.get_value(CONF_PATH), file_path)
        async with aiofiles.open(abs_path, "wb") as _file:
            await _file.write(data)