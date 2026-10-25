# SPDX-License-Identifier: GPL-3.0-only
use strict;
use warnings;
use Test::More;
use FindBin;

our %nrconf = (override_rc => { qr(^ssh) => 1, qr(^gdm) => 0 });
my $loaded = do "$FindBin::Bin/needrestart-droiddeck.conf";
die $@ if $@;
die "Policy not loaded: $!" unless defined $loaded;

sub selection {
    my ($unit) = @_;
    for my $pattern (sort keys %{$nrconf{override_rc}}) {
        return $nrconf{override_rc}->{$pattern} if $unit =~ /$pattern/;
    }
    return undef;
}

for my $unit ('waydroid-container', 'waydroid-container.service',
    'tesla-waydroid-runtime.service', 'tesla-waydroid-compositor.service',
    'tesla-waydroid-headless-session.service', 'tesla-waydroid-display-preview.service',
    'tesla-waydroid-touch-preview.service', 'tesla-waydroid-api-preview.service',
    'tesla-waydroid-audio-preview.service', 'droiddeck-session-recovery.service',
    'droiddeck-fresh-gpu-watch@c686b20e76aa.service',
    'droiddeck-fresh-gpu-worker@c686b20e76aa.service') {
    is(selection($unit), 0, "$unit restart deferred");
}
is(selection('ssh.service'), 1, 'existing SSH restart selection preserved');
is(selection('gdm.service'), 0, 'existing desktop selection preserved');
for my $unit ('apt-daily-upgrade.service', 'unattended-upgrades.service',
    'NetworkManager.service', 'fwupd.service', 'packagekit.service',
    'not-waydroid-container.service', 'waydroid-container-other.service',
    'not-droiddeck-session-recovery.service') {
    ok(!defined selection($unit), "$unit unrelated selection unchanged");
}
is(scalar(keys %{$nrconf{override_rc}}), 5, 'only three scoped rules added');
done_testing();
