"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2024 Lunar (discord itslunar.)
Copyright (C) 2025 Danaelis (discord danaelis.)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

# adds the @locale_doc deco to all commands and groups in POTFILES
with open("POTFILES.in") as f:
    files = f.read().splitlines()

for file in files:
    with open(f"../{file}", "r+") as f:
        stuff = f.read().splitlines()
        stuff2 = stuff
        done = 0
        for idx, line in enumerate(stuff):
            if "@" in line and (".group" in line or ".command" in line):
                stuff2.insert(idx + 1, "    @locale_doc")
                done += 1
        f.seek(0)
        f.write("\n".join(stuff2))
        f.truncate()
