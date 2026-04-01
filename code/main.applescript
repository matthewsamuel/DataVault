on run
	-- Get the directory containing the .app bundle (project root)
	set appPath to POSIX path of (path to me)
	set appDir to do shell script "dirname " & quoted form of appPath
	
	-- Check if server already running
	try
		do shell script "lsof -Pi :8000 -sTCP:LISTEN -t"
		-- Server already running, just open browser
		tell application "Safari"
			activate
			open location "http://127.0.0.1:8000"
		end tell
		display notification "Server was al actief" with title "DataVault"
		return
	end try
	
	-- Start server in background
	display notification "Server wordt gestart..." with title "DataVault"
	
	try
		set logFile to "/tmp/datavault_server.log"
		
		-- Build command with explicit PATH for python/uvicorn (Intel + Apple Silicon)
		set shellScript to "export PATH=\"/opt/homebrew/bin:/usr/local/bin:$PATH\" && "
		set shellScript to shellScript & "cd " & quoted form of appDir & "/code && "
		set shellScript to shellScript & "source venv/bin/activate && "
		set shellScript to shellScript & "nohup python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > " & quoted form of logFile & " 2>&1 & "
		set shellScript to shellScript & "echo $! > /tmp/datavault_server.pid"
		
		do shell script shellScript
		
		-- Wait for server to start (max 15 seconds)
		set serverReady to false
		repeat 30 times
			try
				do shell script "curl -s -o /dev/null -w '' http://127.0.0.1:8000/api/health"
				set serverReady to true
				exit repeat
			on error
				delay 0.5
			end try
		end repeat
		
		-- Small extra delay to ensure server fully serves pages
		delay 1
		
		-- Open browser (gebruik native AppleScript zodat Safari altijd naar voren komt)
		tell application "Safari"
			activate
			open location "http://127.0.0.1:8000"
		end tell
		
		if serverReady then
			display notification "Server gestart!" with title "DataVault"
		else
			display notification "Server openen... (mogelijk nog aan het laden)" with title "DataVault"
		end if
		
	on error errMsg
		display alert "DataVault Error" message "Server kon niet starten: " & errMsg
	end try
end run
