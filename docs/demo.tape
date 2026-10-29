# A recorded terminal session, not a screen capture. Re-record with:
#     vhs docs/demo.tape
# Deterministic by construction: same store, same commands, same timing, so a
# re-record after a code change produces a diffable GIF rather than a new take.

Output docs/demo.gif

Set Shell       zsh
Set FontSize    17
Set Width       1180
Set Height      760
Set Padding     26
Set TypingSpeed 28ms
Set BorderRadius 8

# The enclosure's own palette, so the GIF and the renders are one system:
# near-black plate, teal, brass.
Set Theme { "background": "#0A0D0F", "foreground": "#E9EDEA", "cursor": "#C8A64F", "selection": "#1D2226", "black": "#0A0D0F", "red": "#DE7361", "green": "#6FC0B3", "yellow": "#C8A64F", "blue": "#6FC0B3", "magenta": "#A6AFB4", "cyan": "#2C7A70", "white": "#E9EDEA", "brightBlack": "#748088", "brightRed": "#DE7361", "brightGreen": "#79C182", "brightYellow": "#C8A64F", "brightBlue": "#6FC0B3", "brightMagenta": "#A6AFB4", "brightCyan": "#6FC0B3", "brightWhite": "#F4F3EF" }

Hide
Type "cd software && clear" Enter
Sleep 1s
Show

Sleep 800ms

# Retrieval is bounded spreading activation, not a vector search. This is the
# claim the whole project rests on, and it prints it.
Type "you2 why 'where does she live'"
Sleep 400ms
Enter
Sleep 4s

Type "clear" Enter
Sleep 400ms

# Every memory carries the record of why it is in the state it is in.
Type "you2 inspect 3"
Sleep 400ms
Enter
Sleep 5s

Type "clear" Enter
Sleep 300ms

# Six gates. A render is not a check.
Type "python3 ../cad/docs.py --quick"
Sleep 400ms
Enter
Sleep 3s
