# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014-2017 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Base class for vim-like key sequence parser."""

import enum
import re
import unicodedata

from PyQt5.QtCore import pyqtSignal, QObject
from PyQt5.QtGui import QKeySequence

from qutebrowser.config import config
from qutebrowser.utils import usertypes, log, utils


class BaseKeyParser(QObject):

    """Parser for vim-like key sequences and shortcuts.

    Not intended to be instantiated directly. Subclasses have to override
    execute() to do whatever they want to.

    Class Attributes:
        Match: types of a match between a binding and the keystring.
            partial: No keychain matched yet, but it's still possible in the
                     future.
            definitive: Keychain matches exactly.
            none: No more matches possible.

        Types: type of a key binding.
            chain: execute() was called via a chain-like key binding
            special: execute() was called via a special key binding

        do_log: Whether to log keypresses or not.
        passthrough: Whether unbound keys should be passed through with this
                     handler.

    Attributes:
        bindings: Bound key bindings
        _win_id: The window ID this keyparser is associated with.
        _warn_on_keychains: Whether a warning should be logged when binding
                            keychains in a section which does not support them.
        _sequence: The currently entered key sequence
        _modename: The name of the input mode associated with this keyparser.
        _supports_count: Whether count is supported
        _supports_chains: Whether keychains are supported

    Signals:
        keystring_updated: Emitted when the keystring is updated.
                           arg: New keystring.
        request_leave: Emitted to request leaving a mode.
                       arg 0: Mode to leave.
                       arg 1: Reason for leaving.
                       arg 2: Ignore the request if we're not in that mode
    """

    keystring_updated = pyqtSignal(str)
    request_leave = pyqtSignal(usertypes.KeyMode, str, bool)
    do_log = True
    passthrough = False

    Type = enum.Enum('Type', ['chain', 'special'])

    def __init__(self, win_id, parent=None, supports_count=None,
                 supports_chains=False):
        super().__init__(parent)
        self._win_id = win_id
        self._modename = None
        self._sequence = QKeySequence()
        self._count = ''
        if supports_count is None:
            supports_count = supports_chains
        self._supports_count = supports_count
        self._supports_chains = supports_chains
        self._warn_on_keychains = True
        self.bindings = {}
        config.instance.changed.connect(self._on_config_changed)

    def __repr__(self):
        return utils.get_repr(self, supports_count=self._supports_count,
                              supports_chains=self._supports_chains)

    def _debug_log(self, message):
        """Log a message to the debug log if logging is active.

        Args:
            message: The message to log.
        """
        if self.do_log:
            log.keyboard.debug(message)

    def _handle_key(self, e):
        """Handle a new keypress.

        Separate the keypress into count/command, then check if it matches
        any possible command, and either run the command, ignore it, or
        display an error.

        Args:
            e: the KeyPressEvent from Qt.

        Return:
            A self.Match member.
        """
        key = e.key()
        txt = utils.keyevent_to_string(e)
        self._debug_log("Got key: 0x{:x} / text: '{}'".format(key, txt))

        if txt is None:
            self._debug_log("Ignoring, no text char")
            return QKeySequence.NoMatch

        # if len(txt) == 1:
        #     category = unicodedata.category(txt)
        #     is_control_char = (category == 'Cc')
        # else:
        #     is_control_char = False

        # if (not txt) or is_control_char:
        #     self._debug_log("Ignoring, no text char")
        #     return QKeySequence.NoMatch

        if txt.isdigit():
            assert len(txt) == 1, txt
            self._count += txt
            return None

        sequence = QKeySequence(*self._sequence, e.modifiers() | e.key())
        match, binding = self._match_key(sequence)
        if match == QKeySequence.NoMatch:
            mappings = config.val.bindings.key_mappings
            mapped = mappings.get(txt, None)
            if mapped is not None:
                # FIXME
                raise Exception
                txt = mapped
                sequence = QKeySequence(*self._sequence, e.modifiers() | e.key())
                match, binding = self._match_key(sequence)

        self._sequence = QKeySequence(*self._sequence, e.modifiers() | e.key())
        if match == QKeySequence.ExactMatch:
            self._debug_log("Definitive match for '{}'.".format(
                self._sequence.toString()))
            count = int(self._count) if self._count else None
            self.clear_keystring()
            self.execute(binding, self.Type.chain, count)
        elif match == QKeySequence.PartialMatch:
            self._debug_log("No match for '{}' (added {})".format(
                self._sequence.toString(), txt))
        elif match == QKeySequence.NoMatch:
            self._debug_log("Giving up with '{}', no matches".format(
                self._sequence.toString()))
            self.clear_keystring()
        else:
            raise utils.Unreachable("Invalid match value {!r}".format(match))
        return match

    def _match_key(self, sequence):
        """Try to match a given keystring with any bound keychain.

        Args:
            sequence: The command string to find.

        Return:
            A tuple (matchtype, binding).
                matchtype: Match.definitive, Match.partial or Match.none.
                binding: - None with Match.partial/Match.none.
                         - The found binding with Match.definitive.
        """
        assert sequence

        for seq, cmd in self.bindings.items():
            match = sequence.matches(seq)
            if match != QKeySequence.NoMatch:
                return (match, cmd)

        return (QKeySequence.NoMatch, None)

    def handle(self, e):
        """Handle a new keypress and call the respective handlers.

        Args:
            e: the KeyPressEvent from Qt

        Return:
            True if the event was handled, False otherwise.
        """
        match = self._handle_key(e)

        # FIXME
        # if handled or not self._supports_chains:
        #     return handled

        # don't emit twice if the keystring was cleared in self.clear_keystring
        if self._sequence:
            self.keystring_updated.emit(self._count + self._sequence.toString())

        return match != QKeySequence.NoMatch

    @config.change_filter('bindings')
    def _on_config_changed(self):
        self._read_config()

    def _read_config(self, modename=None):
        """Read the configuration.

        Config format: key = command, e.g.:
            <Ctrl+Q> = quit

        Args:
            modename: Name of the mode to use.
        """
        if modename is None:
            if self._modename is None:
                raise ValueError("read_config called with no mode given, but "
                                 "None defined so far!")
            modename = self._modename
        else:
            self._modename = modename
        self.bindings = {}

        for key, cmd in config.key_instance.get_bindings_for(modename).items():
            assert cmd
            self.bindings[key] = cmd

    def _parse_key_command(self, modename, key, cmd):
        """Parse the keys and their command and store them in the object."""
        # FIXME
        # elif self._warn_on_keychains:
        #     log.keyboard.warning("Ignoring keychain '{}' in mode '{}' because "
        #                          "keychains are not supported there."
        #                         .format(key, modename))

    def execute(self, cmdstr, keytype, count=None):
        """Handle a completed keychain.

        Args:
            cmdstr: The command to execute as a string.
            keytype: Type.chain or Type.special
            count: The count if given.
        """
        raise NotImplementedError

    def clear_keystring(self):
        """Clear the currently entered key sequence."""
        if self._sequence:
            self._debug_log("discarding keystring '{}'.".format(
                self._sequence.toString()))
            self._sequence = QKeySequence()
            self._count = ''
            self.keystring_updated.emit('')
