Your plugin's translations reside here. This plugin uses pyproject.toml for
packaging, so translations are managed with pybabel (Babel) directly rather than
via setup.py commands. The most common workflow:

Extract translatable strings into the template (messages.pot):
    pybabel extract -F babel.cfg -o translations/messages.pot octoprint_autoconnectplus

Create a new locale catalog (e.g. de):
    pybabel init -i translations/messages.pot -d translations -l de

Update an existing catalog after the template changed:
    pybabel update -i translations/messages.pot -d translations -l de

Compile catalogs into the .mo files OctoPrint loads at runtime:
    pybabel compile -d translations -l de

To bundle a translation with the plugin (so it ships with it), the compiled
catalog must live under octoprint_autoconnectplus/translations/<locale>/LC_MESSAGES/.
Copy the compiled .po/.mo there, e.g.:
    mkdir -p octoprint_autoconnectplus/translations/de/LC_MESSAGES
    cp translations/de/LC_MESSAGES/messages.{po,mo} \
       octoprint_autoconnectplus/translations/de/LC_MESSAGES/

Note: the babel.cfg in this repo still lists the legacy Jinja2 extension names
(jinja2.ext.autoescape / with_) for compatibility with OctoPrint's own Babel
setup. With a newer standalone Jinja2 these are built-in; if extraction fails,
extract with a minimal config containing only:
    [python: */**.py]
    [jinja2: */**.jinja2]
