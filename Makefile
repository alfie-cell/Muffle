PYTHON  ?= python3
# Rule profile: minimal (~17k rules, 9 MB compiled), lean (~70k, 26 MB, default), full (~120k, 45 MB)
PROFILE ?= lean
PROJECT  = Muffle.xcodeproj
CONFIG  ?= Release
# Safari only loads extensions whose signature has an Apple-issued certificate chain
# (ad-hoc signing is rejected by amfid with -67050 and the extension never gets its request).
# TEAM is auto-detected from the first "Apple Development" identity in the keychain, which
# Xcode creates once an Apple ID (free is fine) is added under Xcode > Settings > Accounts.
# Override with: make install TEAM=ABCDE12345
TEAM    ?= $(shell security find-certificate -c 'Apple Development' -p 2>/dev/null | openssl x509 -noout -subject 2>/dev/null | sed -nE 's/.*OU ?= ?([A-Z0-9]{10}).*/\1/p')

ifeq ($(TEAM),)
$(warning No Apple Development certificate found; building ad-hoc signed. Safari will NOT load the extensions. Add your Apple ID in Xcode > Settings > Accounts, then Manage Certificates > + > Apple Development.)
SIGN = CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY=- DEVELOPMENT_TEAM= PROVISIONING_PROFILE_SPECIFIER=
else
SIGN = CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=$(TEAM) -allowProvisioningUpdates
endif

.PHONY: rules rules-refresh build run install reload status update schedule unschedule test clean

rules:
	$(PYTHON) tools/build_rules.py --profile $(PROFILE)

rules-refresh:
	$(PYTHON) tools/build_rules.py --profile $(PROFILE) --refresh

build:
	xcodebuild -project $(PROJECT) -target Muffle -configuration $(CONFIG) $(SIGN) build

run: build
	open build/$(CONFIG)/Muffle.app

install: build
	# Unregister the old copy before deleting it, or pkd keeps a stale record that wins over the new one.
	-pluginkit -r /Applications/Muffle.app/Contents/PlugIns/Blocker.appex 2>/dev/null
	-pluginkit -r /Applications/Muffle.app/Contents/PlugIns/YouTube.appex 2>/dev/null
	rm -rf /Applications/Muffle.app
	cp -R build/$(CONFIG)/Muffle.app /Applications/Muffle.app
	# The build copy must not stay registered, or Safari may pick it over /Applications.
	-pluginkit -r build/$(CONFIG)/Muffle.app/Contents/PlugIns/Blocker.appex 2>/dev/null
	-pluginkit -r build/$(CONFIG)/Muffle.app/Contents/PlugIns/YouTube.appex 2>/dev/null
	rm -rf build
	sleep 2
	pluginkit -a /Applications/Muffle.app/Contents/PlugIns/Blocker.appex
	pluginkit -a /Applications/Muffle.app/Contents/PlugIns/YouTube.appex
	sleep 2
	@pluginkit -m -i $$(defaults read /Applications/Muffle.app/Contents/Info.plist CFBundleIdentifier).blocker | grep -q . || pluginkit -a /Applications/Muffle.app/Contents/PlugIns/Blocker.appex
	@pluginkit -m -i $$(defaults read /Applications/Muffle.app/Contents/Info.plist CFBundleIdentifier).youtube | grep -q . || pluginkit -a /Applications/Muffle.app/Contents/PlugIns/YouTube.appex
	@echo "registered: $$(pluginkit -m -i $$(defaults read /Applications/Muffle.app/Contents/Info.plist CFBundleIdentifier).blocker | wc -l | tr -d ' ') blocker, $$(pluginkit -m -i $$(defaults read /Applications/Muffle.app/Contents/Info.plist CFBundleIdentifier).youtube | wc -l | tr -d ' ') youtube"

# Ask Safari to reload the content blocker rules and report the result (needs the app installed).
reload:
	/Applications/Muffle.app/Contents/MacOS/Muffle --reload

# Print whether Safari has each extension enabled.
status:
	/Applications/Muffle.app/Contents/MacOS/Muffle --status

# Refresh lists and push new rules to the installed extension without rebuilding.
update:
	PROFILE=$(PROFILE) tools/update.sh

# Run `make update` every Monday at 10:00 via launchd (runs at next opportunity if the Mac was asleep).
AGENT = $(HOME)/Library/LaunchAgents/com.example.muffle.update.plist
schedule:
	mkdir -p $(HOME)/Library/LaunchAgents
	sed -e 's|__PROJECT__|$(CURDIR)|g' -e 's|__HOME__|$(HOME)|g' tools/com.example.muffle.update.plist > $(AGENT)
	-launchctl bootout gui/$$(id -u) $(AGENT) 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(AGENT)
	launchctl print gui/$$(id -u)/com.example.muffle.update | grep -E 'state|program' | head -3

unschedule:
	-launchctl bootout gui/$$(id -u) $(AGENT) 2>/dev/null
	rm -f $(AGENT)

test:
	$(PYTHON) -m unittest discover -s tools -p 'test_*.py'

clean:
	rm -rf build
