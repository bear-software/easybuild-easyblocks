##
# Copyright 2009-2020 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/easybuilders/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
EasyBuild support for installing MATLAB, implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Fotis Georgatos (Uni.Lu, NTUA)
"""
import re
import os
import shutil
import stat
import tempfile

from distutils.version import LooseVersion

from easybuild.easyblocks.generic.packedbinary import PackedBinary
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import adjust_permissions, change_dir, read_file, write_file
from easybuild.tools.py2vs3 import string_type
from easybuild.tools.run import run_cmd
from easybuild.tools.systemtools import get_shared_lib_ext


class EB_MATLAB(PackedBinary):
    """Support for installing MATLAB."""

    def __init__(self, *args, **kwargs):
        """Add extra config options specific to MATLAB."""
        super(EB_MATLAB, self).__init__(*args, **kwargs)
        self.comp_fam = None
        self.configfile = os.path.join(self.builddir, 'my_installer_input.txt')

    @staticmethod
    def extra_options():
        extra_vars = {
            'java_options': ['-Xmx256m', "$_JAVA_OPTIONS value set for install and in module file.", CUSTOM],
            'key': [None, "Installation key(s), make one install for each key. Single key or a list of keys", CUSTOM],
        }
        return PackedBinary.extra_options(extra_vars)

    def configure_step(self):
        """Configure MATLAB installation: create license file."""

        licfile = self.cfg['license_file']
        if licfile is None:
            licserv = self.cfg['license_server']
            if licserv is None:
                licserv = os.getenv('EB_MATLAB_LICENSE_SERVER', 'license.example.com')
            licport = self.cfg['license_server_port']
            if licport is None:
                licport = os.getenv('EB_MATLAB_LICENSE_SERVER_PORT', '00000')
            # create license file
            lictxt = '\n'.join([
                "SERVER %s 000000000000 %s" % (licserv, licport),
                "USE_SERVER",
            ])

            licfile = os.path.join(self.builddir, 'matlab.lic')
            write_file(licfile, lictxt)

        try:
            shutil.copyfile(os.path.join(self.cfg['start_dir'], 'installer_input.txt'), self.configfile)
            config = read_file(self.configfile)

            regdest = re.compile(r"^# destinationFolder=.*", re.M)
            regagree = re.compile(r"^# agreeToLicense=.*", re.M)
            regmode = re.compile(r"^# mode=.*", re.M)
            reglicpath = re.compile(r"^# licensePath=.*", re.M)

            config = regdest.sub("destinationFolder=%s" % self.installdir, config)
            config = regagree.sub("agreeToLicense=Yes", config)
            config = regmode.sub("mode=silent", config)
            config = reglicpath.sub("licensePath=%s" % licfile, config)

            write_file(self.configfile, config)

        except IOError as err:
            raise EasyBuildError("Failed to create installation config file %s: %s", self.configfile, err)

        self.log.debug('configuration file written to %s:\n %s', self.configfile, config)

    def install_step(self):
        """MATLAB install procedure using 'install' command."""

        src = os.path.join(self.cfg['start_dir'], 'install')

        # make sure install script is executable
        adjust_permissions(src, stat.S_IXUSR)

        if LooseVersion(self.version) >= LooseVersion('2016b'):
            jdir = os.path.join(self.cfg['start_dir'], 'sys', 'java', 'jre', 'glnxa64', 'jre', 'bin')
            for perm_dir in [os.path.join(self.cfg['start_dir'], 'bin', 'glnxa64'), jdir]:
                adjust_permissions(perm_dir, stat.S_IXUSR)

        # make sure $DISPLAY is not defined, which may lead to (hard to trace) problems
        # this is a workaround for not being able to specify --nodisplay to the install scripts
        if 'DISPLAY' in os.environ:
            os.environ.pop('DISPLAY')

        if '_JAVA_OPTIONS' not in self.cfg['preinstallopts']:
            java_opts = 'export _JAVA_OPTIONS="%s" && ' % self.cfg['java_options']
            self.cfg['preinstallopts'] = java_opts + self.cfg['preinstallopts']
        if LooseVersion(self.version) >= LooseVersion('2016b'):
            change_dir(self.builddir)

        # MATLAB installer ignores TMPDIR (always uses /tmp) and might need a large tmpdir
        tmpdir = "-tmpdir %s" % tempfile.mkdtemp()

        keys = self.cfg['key']
        if keys is None:
            keys = os.getenv('EB_MATLAB_KEY', '00000-00000-00000-00000-00000-00000-00000-00000-00000-00000')
        if isinstance(keys, string_type):
            keys = keys.split(',')

        # Make one install for each key
        for i, key in enumerate(keys):

            self.log.debug('Installing with key %s of %s', i + 1, len(keys))

            if LooseVersion(self.version) >= LooseVersion('2020a'):
                self.log.debug('Version is %s - using binary installer method', self.version)
                try:
                    with tempfile.NamedTemporaryFile() as fd:
                        tmp_configfile = fd.name
                        with open(self.configfile) as template_fd:
                            tmp_config = template_fd.read()
                        tmp_config = tmp_config.replace('# fileInstallationKey=', 'fileInstallationKey=%s' % key)
                        fd.write(tmp_config)

                        self.log.debug('temp config file written to %s:\n %s', tmp_configfile, tmp_config)

                        cmd = ' '.join([
                            self.cfg['preinstallopts'],
                            src,
                            '-inputFile',
                            tmp_configfile,
                            self.cfg['installopts'],
                        ])

                        (out, _) = run_cmd(cmd, log_all=True, simple=False)

                except IOError as err:
                    raise EasyBuildError("Failed to create temporary config file %s: %s", tmp_configfile, err)

            else:
                self.log.debug('Version is %s - using script installer method', self.version)
                cmd = ' '.join([
                    self.cfg['preinstallopts'],
                    src,
                    '-v',
                    tmpdir,
                    '-inputFile',
                    self.configfile,
                    '-fileInstallationKey',
                    key,
                    self.cfg['installopts'],
                ])

                (out, _) = run_cmd(cmd, log_all=True, simple=False)

            # check installer output for known signs of trouble
            patterns = [
                "Error: You have entered an invalid File Installation Key",
            ]

            for pattern in patterns:
                regex = re.compile(pattern, re.I)
                if regex.search(out):
                    raise EasyBuildError("Found error pattern '%s' in output of installation command '%s': %s",
                                         regex.pattern, cmd, out)

    def sanity_check_step(self):
        """Custom sanity check for MATLAB."""
        custom_paths = {
            'files': ["bin/matlab", "bin/glnxa64/MATLAB", "toolbox/local/classpath.txt"],
            'dirs': ["java/jar"],
        }
        super(EB_MATLAB, self).sanity_check_step(custom_paths=custom_paths)

    def make_module_extra(self):
        """Extend PATH and set proper _JAVA_OPTIONS (e.g., -Xmx)."""
        txt = super(EB_MATLAB, self).make_module_extra()

        # make MATLAB runtime available
        if LooseVersion(self.version) >= LooseVersion('2017a'):
            for ldlibdir in ['runtime', 'bin', os.path.join('sys', 'os')]:
                libdir = os.path.join(ldlibdir, 'glnxa64')
                txt += self.module_generator.prepend_paths('LD_LIBRARY_PATH', libdir)
        if self.cfg['java_options']:
            txt += self.module_generator.set_environment('_JAVA_OPTIONS', self.cfg['java_options'])
        return txt
