#!/usr/bin/perl
#
# Mammotion Mower — LoxBerry plugin CGI.
#
# Renders inside the LoxBerry shell (lbheader/lbfooter), reads/writes
# config/default.json, restarts the daemon on save. Mirrors the
# loxberry-integrator skill's reference baseline.

use strict;
use warnings;

use CGI;
use HTML::Template;
use LoxBerry::JSON;
use LoxBerry::System;
use LoxBerry::Web;

my $cgi      = CGI->new;
my $version  = "0.1.0";
my $cfgfile  = "$lbpconfigdir/default.json";
my $cfgobj   = LoxBerry::JSON->new();
my $cfg      = -f $cfgfile ? $cfgobj->open(filename => $cfgfile) : {};
my $saved    = 0;
my $errormsg = "";

# ---------------------------------------------------------------- save ---
if ($cgi->request_method eq "POST" && defined $cgi->param("save")) {

    $cfg->{enabled} = $cgi->param("enabled") ? 1 : 0;

    my $form_email = scalar $cgi->param("account_email") // "";
    $form_email    =~ s/^\s+|\s+$//g;
    $cfg->{account_email} = $form_email;

    my $new_password = scalar $cgi->param("account_password");
    if (defined $new_password && $new_password ne "") {
        $new_password =~ s/^\s+|\s+$//g;
        $cfg->{account_password} = $new_password if $new_password ne "";
    }

    my $poll = int($cgi->param("poll_interval_seconds") || 60);
    $poll = 10   if $poll < 10;
    $poll = 3600 if $poll > 3600;
    $cfg->{poll_interval_seconds} = $poll;

    $cfg->{use_loxberry_mqtt}          = $cgi->param("use_loxberry_mqtt")          ? 1 : 0;
    $cfg->{register_mqtt_subscription} = $cgi->param("register_mqtt_subscription") ? 1 : 0;
    $cfg->{enable_commands}            = $cgi->param("enable_commands")            ? 1 : 0;

    my $prefix = scalar $cgi->param("mqtt_topic_prefix") // "mammotion";
    $prefix =~ s/^\s+|\s+$//g;
    $prefix =~ s{[^A-Za-z0-9_.-]+}{_}g;
    $prefix = "mammotion" if $prefix eq "";
    $cfg->{mqtt_topic_prefix} = $prefix;

    my $suffix = scalar $cgi->param("command_topic_suffix") // "set";
    $suffix =~ s/^\s+|\s+$//g;
    $suffix =~ s{[^A-Za-z0-9_.-]+}{_}g;
    $suffix = "set" if $suffix eq "";
    $cfg->{command_topic_suffix} = $suffix;

    # Manual MQTT broker fallback fields
    $cfg->{mqtt_host} = scalar $cgi->param("mqtt_host") || "localhost";
    my $mqtt_port = int($cgi->param("mqtt_port") || 1883);
    $mqtt_port = 1   if $mqtt_port < 1;
    $mqtt_port = 65535 if $mqtt_port > 65535;
    $cfg->{mqtt_port} = $mqtt_port;
    $cfg->{mqtt_username} = scalar $cgi->param("mqtt_username") || "";
    my $new_mqtt_pw = scalar $cgi->param("mqtt_password");
    if (defined $new_mqtt_pw && $new_mqtt_pw ne "") {
        $new_mqtt_pw =~ s/^\s+|\s+$//g;
        $cfg->{mqtt_password} = $new_mqtt_pw if $new_mqtt_pw ne "";
    }

    $cfg->{debug} = $cgi->param("debug") ? 1 : 0;

    if (eval { $cfgobj->write(); 1 }) {
        $saved = 1;
        chmod 0600, $cfgfile;

        # Restart the daemon. The installer drops the hook here; redirect
        # subshell output to a log file or it leaks into the HTTP response.
        my $daemon = "/opt/loxberry/system/daemons/plugins/mammotion-mower";
        if (-x $daemon) {
            system("$daemon restart >>'$lbplogdir/daemon-restart.log' 2>&1");
        }
    } else {
        $errormsg = "Could not save configuration: $@";
    }
}

# ------------------------------------------------------------ status ---
my $daemon_state   = "unknown";
my $daemon_running = 0;
my $pidfile        = "$lbplogdir/mammotion-mower.pid";
my $creds_missing  = !($cfg->{account_email} && $cfg->{account_password});

if (-f $pidfile) {
    my $pid = do { local (@ARGV, $/) = $pidfile; <> };
    chomp $pid if defined $pid;
    if ($pid && kill(0, $pid)) {
        $daemon_state   = "running (PID $pid)";
        $daemon_running = 1;
    } else {
        $daemon_state = "stopped (stale pidfile)";
    }
} elsif (!$cfg->{enabled}) {
    $daemon_state = "disabled";
} elsif ($creds_missing) {
    $daemon_state = "not configured (enter credentials)";
} else {
    $daemon_state = "stopped";
}

my $logfile  = "$lbplogdir/mammotion-mower.log";
my $log_size = -f $logfile ? (-s $logfile) : 0;

# ---------- LoxBerry MQTT broker auto-discovery ----------
my $lb_mqtt_host      = "";
my $lb_mqtt_port      = "";
my $lb_mqtt_user      = "";
my $lb_mqtt_available = 0;
eval {
    require LoxBerry::IO;
    my $cred = LoxBerry::IO::mqtt_connectiondetails();
    if ($cred && $cred->{brokerhost}) {
        $lb_mqtt_host      = $cred->{brokerhost};
        $lb_mqtt_port      = $cred->{brokerport};
        $lb_mqtt_user      = $cred->{brokeruser} || "";
        $lb_mqtt_available = 1;
    }
};

# --------------------------------------------------------------- render ---
print $cgi->header(-type => "text/html", -charset => "utf-8");

my $template = HTML::Template->new(
    filename          => "$lbptemplatedir/settings.html",
    die_on_bad_params => 0,
    loop_context_vars => 1,
    global_vars       => 1,
);

$template->param(
    TITLE                 => "Mammotion Mower",
    SAVED                 => $saved,
    ERROR                 => $errormsg,
    DAEMON_STATE          => $daemon_state,
    DAEMON_RUNNING        => $daemon_running,
    LOG_SIZE              => $log_size,
    LOG_PATH              => $logfile,
    ENABLED               => $cfg->{enabled} ? 1 : 0,
    ACCOUNT_EMAIL         => $cfg->{account_email} || "",
    PASSWORD_SET          => ($cfg->{account_password} && length $cfg->{account_password}) ? 1 : 0,
    POLL_INTERVAL_SECONDS => $cfg->{poll_interval_seconds} || 60,
    USE_LOXBERRY_MQTT     => (exists $cfg->{use_loxberry_mqtt} ? ($cfg->{use_loxberry_mqtt} ? 1 : 0) : 1),
    LB_MQTT_AVAILABLE     => $lb_mqtt_available,
    LB_MQTT_HOST          => $lb_mqtt_host,
    LB_MQTT_PORT          => $lb_mqtt_port,
    LB_MQTT_USER          => $lb_mqtt_user,
    MQTT_HOST             => $cfg->{mqtt_host} || "localhost",
    MQTT_PORT             => $cfg->{mqtt_port} || 1883,
    MQTT_USERNAME         => $cfg->{mqtt_username} || "",
    MQTT_PASSWORD_SET     => ($cfg->{mqtt_password} && length $cfg->{mqtt_password}) ? 1 : 0,
    MQTT_TOPIC_PREFIX     => $cfg->{mqtt_topic_prefix} || "mammotion",
    REGISTER_MQTT_SUB     => (exists $cfg->{register_mqtt_subscription} ? ($cfg->{register_mqtt_subscription} ? 1 : 0) : 1),
    ENABLE_COMMANDS       => (exists $cfg->{enable_commands} ? ($cfg->{enable_commands} ? 1 : 0) : 1),
    COMMAND_TOPIC_SUFFIX  => $cfg->{command_topic_suffix} || "set",
    DEBUG                 => $cfg->{debug} ? 1 : 0,
    VERSION               => $version,
    SELF_URL              => $ENV{REQUEST_URI} || "",
);

LoxBerry::Web::lbheader("Mammotion Mower", "https://github.com/jovd83/LoxBerry-Plugin-mammotion-mower", "");
print $template->output;
LoxBerry::Web::lbfooter();

exit 0;
