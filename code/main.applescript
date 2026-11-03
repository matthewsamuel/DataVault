on run
	-- Get the directory containing the .app bundle (project root)
	set appPath to POSIX path of (path to me)
	set appDir to do shell script "dirname " & quoted form of appPath
	set codeDir to appDir & "/code"

	set vaultURL to "http://127.0.0.1:8000"

	-- Browser from config.json ("browser" key), Safari by default
	set browserName to "Safari"
	try
		set browserName to do shell script "plutil -extract browser raw -o - " & quoted form of (codeDir & "/config.json")
	end try

	-- Check if server already running
	try
		do shell script "lsof -Pi :8000 -sTCP:LISTEN -t"
		-- Server already running, just open browser
		my openVault(vaultURL, browserName)
		display notification "Server was al actief" with title "DataVault"
		return
	end try

	-- The venv must exist; start.sh creates it on first run
	try
		do shell script "test -x " & quoted form of (codeDir & "/venv/bin/python")
	on error
		display alert "DataVault" message "De Python-omgeving ontbreekt. Voer eenmalig code/start.sh uit in Terminal; daarna werkt de app."
		return
	end try

	-- Start server in background
	display notification "Server wordt gestart..." with title "DataVault"

	try
		-- Not in /tmp (readable by every user): the user's own Logs folder, readable only by the user
		set logDir to (POSIX path of (path to home folder)) & "Library/Logs/DataVault"
		set logFile to logDir & "/server.log"

		-- Explicit interpreter path: no dependency on shell activation or PATH
		set shellScript to "export PATH=\"/opt/homebrew/bin:/usr/local/bin:$PATH\" && "
		set shellScript to shellScript & "mkdir -p " & quoted form of logDir & " && chmod 700 " & quoted form of logDir & " && umask 077 && "
		set shellScript to shellScript & "cd " & quoted form of codeDir & " && "
		set shellScript to shellScript & "nohup ./venv/bin/python -m uvicorn --factory backend.main:create_app --host 127.0.0.1 --port 8000 > " & quoted form of logFile & " 2>&1 & "
		set shellScript to shellScript & "echo $! > /tmp/datavault_server.pid"

		do shell script shellScript

		-- Wait for server to start (max 15 seconds)
		set serverReady to false
		repeat 30 times
			try
				do shell script "curl -sf -o /dev/null http://127.0.0.1:8000/api/health"
				set serverReady to true
				exit repeat
			on error
				delay 0.5
			end try
		end repeat

		if serverReady then
			my openVault(vaultURL, browserName)
			display notification "Server gestart!" with title "DataVault"
		else
			set lastLines to do shell script "tail -n 15 " & quoted form of logFile & " 2>/dev/null || true"
			display alert "DataVault kon niet starten" message "Zie " & logFile & ":" & return & return & lastLines
		end if

	on error errMsg
		display alert "DataVault Error" message "Server kon niet starten: " & errMsg
	end try
end run

on openVault(vaultURL, browserName)
	if browserName is "Safari" then
		my openVaultInSafari(vaultURL)
	else
		try
			do shell script "open -a " & quoted form of browserName & " " & quoted form of vaultURL
		on error
			do shell script "open " & quoted form of vaultURL
		end try
	end if
end openVault

on openVaultInSafari(vaultURL)
	tell application "Safari"
		activate

		-- Search for existing tab with vault URL
		set foundTab to false
		set targetWindow to missing value
		set targetTab to missing value

		repeat with w in windows
			set tabIndex to 0
			repeat with t in tabs of w
				set tabIndex to tabIndex + 1
				if URL of t starts with vaultURL then
					set foundTab to true
					set targetWindow to w
					set targetTab to tabIndex
					exit repeat
				end if
			end repeat
			if foundTab then exit repeat
		end repeat

		if foundTab then
			-- Bring existing tab to front
			set current tab of targetWindow to tab targetTab of targetWindow
			set index of targetWindow to 1
		else
			-- Open new tab in front window, or new window if none exists
			if (count of windows) > 0 then
				tell front window
					set current tab to (make new tab with properties {URL:vaultURL})
				end tell
			else
				make new document with properties {URL:vaultURL}
			end if
		end if
	end tell
end openVaultInSafari
