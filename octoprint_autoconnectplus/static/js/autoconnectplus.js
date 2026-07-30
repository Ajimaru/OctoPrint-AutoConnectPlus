/*
 * View model for OctoPrint-AutoConnectPlus
 *
 * The settings template uses custom_bindings=True and is bound to this view
 * model, which exposes the shared settingsViewModel as `settings` (so inputs
 * bind to settings.settings.plugins.autoconnectplus.*) plus observables for
 * the detected connection. The latter are refreshed from the plugin's simple
 * API every time the settings dialog is shown, so the display never goes
 * stale after the preferred connection changes.
 */
$(function () {
    function AutoConnectPlusViewModel(parameters) {
        var self = this;

        self.settings = parameters[0];

        self.detectedLabel = ko.observable("");
        self.detectedTarget = ko.observable("");
        self.detectedWarning = ko.observable("");

        self.refreshDetected = function () {
            OctoPrint.simpleApiGet("autoconnectplus").done(function (data) {
                self.detectedLabel(data.label || "");
                self.detectedTarget(data.target || "");
                self.detectedWarning(data.warning || "");
            });
        };

        self.onSettingsShown = function () {
            self.refreshDetected();
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: AutoConnectPlusViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_autoconnectplus"],
    });
});
