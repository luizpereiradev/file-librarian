-- PROTOTYPE, NOT SHIPPED. Spike for issue #111; see docs/decisions/0004-menu-bar-item.md.
--
-- The same menu bar item as ../swift/main.swift, as an AppleScriptObjC stay-open
-- applet: osacompile -s -o <app> menu.applescript. No compiler needed.
-- AppleScriptObjC cannot take blocks, so there is no kqueue watcher: the idle
-- handler re-reads state.json every idleSeconds seconds (cheap: one stat, a read only
-- when the modification date changed).

use AppleScript version "2.4"
use framework "Foundation"
use framework "AppKit"
use scripting additions

property idleSeconds : 30
property statusItem : missing value
property theMenu : missing value
property lineItem : missing value
property stateDir : missing value
property lastStamp : missing value
property refreshes : 0

on run
	set ca to current application
	set info to ca's NSBundle's mainBundle()'s infoDictionary()
	set stateDir to (info's objectForKey:"FlibMenuStateDir") as text
	ca's NSFileManager's defaultManager()'s createDirectoryAtPath:stateDir withIntermediateDirectories:true attributes:(missing value) |error|:(missing value)
	set statusItem to ca's NSStatusBar's systemStatusBar()'s statusItemWithLength:(ca's NSVariableStatusItemLength)
	statusItem's button()'s setImagePosition:(ca's NSImageLeading)
	set theMenu to ca's NSMenu's alloc()'s init()
	set lineItem to ca's NSMenuItem's alloc()'s initWithTitle:"…" action:(missing value) keyEquivalent:""
	lineItem's setEnabled:false
	theMenu's addItem:lineItem
	theMenu's addItem:(ca's NSMenuItem's separatorItem())
	my addAction("Organize now", "organize:")
	my addAction("Open review folder", "openReview:")
	theMenu's addItem:(ca's NSMenuItem's separatorItem())
	my addAction("Quit flib-menu-test", "quitApp:")
	statusItem's setMenu:theMenu
	my refresh(true)
	my writeLog("started")
end run

on addAction(title, selectorName)
	set entry to current application's NSMenuItem's alloc()'s initWithTitle:title action:selectorName keyEquivalent:""
	entry's setTarget:me
	theMenu's addItem:entry
end addAction

on idle
	try
		my refresh(false)
	end try
	return idleSeconds
end idle

-- `open -g <app>` while it runs lands here (a reopen event from Launch Services,
-- no Automation permission involved): flib's nudge right after writing state.json.
on reopen
	my writeLog("reopen")
	try
		my refresh(false)
	end try
end reopen

on refresh(force)
	set ca to current application
	set fm to ca's NSFileManager's defaultManager()
	set request to stateDir & "/selftest.request"
	if (fm's fileExistsAtPath:request) as boolean then
		fm's removeItemAtPath:request |error|:(missing value)
		my selftest()
	end if
	set quitRequest to stateDir & "/quit.request"
	if (fm's fileExistsAtPath:quitRequest) as boolean then
		fm's removeItemAtPath:quitRequest |error|:(missing value)
		my writeLog("quit.request: choosing Quit from the menu")
		theMenu's performActionForItemAtIndex:((theMenu's numberOfItems()) - 1)
	end if
	set statePath to stateDir & "/state.json"
	set attrs to fm's attributesOfItemAtPath:statePath |error|:(missing value)
	set stamp to "none"
	if attrs is not missing value then set stamp to ((attrs's fileModificationDate()'s timeIntervalSince1970()) as real) as text
	if (not force) and stamp = lastStamp then return
	set lastStamp to stamp
	set refreshes to refreshes + 1
	set pose to "trouble"
	set countValue to 0
	set lineText to "flib has not written its state yet"
	set theData to ca's NSData's dataWithContentsOfFile:statePath
	if theData is not missing value then
		set state to ca's NSJSONSerialization's JSONObjectWithData:theData options:0 |error|:(missing value)
		if state is not missing value then
			set p to (state's objectForKey:"pose")
			if p is not missing value and ({"asleep", "busy", "due", "attention", "trouble"} contains (p as text)) then set pose to p as text
			set c to (state's objectForKey:"count")
			if c is not missing value then set countValue to c as integer
			set l to (state's objectForKey:"line")
			if l is not missing value then set lineText to l as text
		end if
	end if
	set iconPath to (ca's NSBundle's mainBundle()'s pathForResource:pose ofType:"svg")
	set img to missing value
	if iconPath is not missing value then set img to ca's NSImage's alloc()'s initWithContentsOfFile:iconPath
	if img is not missing value then
		img's setTemplate:true
		img's setSize:{18, 18}
	end if
	statusItem's button()'s setImage:img
	if countValue > 0 then
		statusItem's button()'s setTitle:(countValue as text)
	else
		statusItem's button()'s setTitle:""
	end if
	lineItem's setTitle:lineText
end refresh

on organize:sender
	my runTool("organize")
end organize:

on openReview:sender
	my runTool("open-review")
end openReview:

on quitApp:sender
	quit
end quitApp:

-- A fixed executable and fixed arguments through NSTask: no shell.
on runTool(label)
	set ca to current application
	set task to ca's NSTask's alloc()'s init()
	task's setExecutableURL:(ca's NSURL's fileURLWithPath:"/bin/date")
	task's setArguments:{"+" & label & " %s"}
	set out to stateDir & "/actions.log"
	set fm to ca's NSFileManager's defaultManager()
	if not ((fm's fileExistsAtPath:out) as boolean) then fm's createFileAtPath:out |contents|:(missing value) attributes:(missing value)
	set handle to ca's NSFileHandle's fileHandleForWritingAtPath:out
	handle's seekToEndOfFile()
	task's setStandardOutput:handle
	task's launchAndReturnError:(missing value)
end runTool

on writeLog(textValue)
	set ca to current application
	set out to stateDir & "/app.log"
	set fm to ca's NSFileManager's defaultManager()
	if not ((fm's fileExistsAtPath:out) as boolean) then fm's createFileAtPath:out |contents|:(missing value) attributes:(missing value)
	set handle to ca's NSFileHandle's fileHandleForWritingAtPath:out
	handle's seekToEndOfFile()
	set entry to (((current date) as text) & " " & textValue & " pid " & ((ca's NSProcessInfo's processInfo()'s processIdentifier()) as text) & linefeed)
	handle's writeData:((ca's NSString's stringWithString:entry)'s dataUsingEncoding:(ca's NSUTF8StringEncoding))
	handle's closeFile()
end writeLog

-- ---- measurement only ----
on selftest()
	set ca to current application
	set btn to statusItem's button()
	set report to ca's NSMutableDictionary's dictionary()
	report's setObject:(ca's NSApp's activationPolicy()) forKey:"activation_policy"
	report's setObject:(statusItem's isVisible()) forKey:"visible"
	report's setObject:(btn's effectiveAppearance()'s |name|()) forKey:"effective_appearance"
	report's setObject:(btn's title()) forKey:"title"
	report's setObject:refreshes forKey:"refreshes"
	if btn's image() is not missing value then report's setObject:(btn's image()'s isTemplate()) forKey:"image_is_template"
	set win to btn's |window|()
	if win is not missing value then
		report's setObject:((ca's NSValue's valueWithRect:(win's frame()))'s |description|()) forKey:"frame"
		report's setObject:(((win's occlusionState()) as integer) div 2 mod 2 = 1) forKey:"on_screen"
	end if
	repeat with pair in {{"light", ca's NSAppearanceNameAqua}, {"dark", ca's NSAppearanceNameDarkAqua}}
		btn's setAppearance:(ca's NSAppearance's appearanceNamed:(item 2 of pair))
		btn's |display|()
		set rep to btn's bitmapImageRepForCachingDisplayInRect:(btn's |bounds|())
		btn's cacheDisplayInRect:(btn's |bounds|()) toBitmapImageRep:rep
		set png to rep's representationUsingType:(ca's NSBitmapImageFileTypePNG) |properties|:(ca's NSDictionary's dictionary())
		png's writeToFile:(stateDir & "/button-" & (item 1 of pair) & ".png") atomically:true
	end repeat
	btn's setAppearance:(missing value)
	set idx to theMenu's indexOfItemWithTitle:"Organize now"
	theMenu's performActionForItemAtIndex:idx
	report's setObject:"Organize now" forKey:"fired"
	set json to ca's NSJSONSerialization's dataWithJSONObject:report options:1 |error|:(missing value)
	json's writeToFile:(stateDir & "/selftest.json") atomically:true
end selftest

on quit
	try
		current application's NSStatusBar's systemStatusBar()'s removeStatusItem:statusItem
	end try
	continue quit
end quit
