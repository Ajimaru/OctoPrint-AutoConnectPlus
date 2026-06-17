/*
 * View model for OctoPrint-AutoConnectPlus
 *
 * The settings template uses custom_bindings=False and binds directly against
 * the shared settingsViewModel observables (settings.plugins.autoconnectplus.*),
 * with Knockout `visible` bindings driving the per-connection-type field groups.
 *
 * No additional client logic is required, but a view model is registered here
 * so the asset is loaded and future client-side behaviour has a home.
 */
$(function () {
    function AutoConnectPlusViewModel(parameters) {
        var self = this;

        self.settingsViewModel = parameters[0];
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: AutoConnectPlusViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_autoconnectplus"],
    });
});
