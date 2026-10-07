tell application "System Events"
	tell process "NeteaseMusic"
		set out to ""
		set elems to entire contents of window 1
		repeat with el in elems
			try
				set d to description of el
				if d is not missing value and d contains "播" then
					set p to position of el
					set out to out & d & "@" & (item 1 of p) & "," & (item 2 of p) & "|"
				end if
			end try
		end repeat
		return out
	end tell
end tell