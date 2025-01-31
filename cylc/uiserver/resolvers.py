# Copyright (C) NIWA & British Crown (Met Office) & Contributors.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""GraphQL resolvers for use in data accessing and mutation of workflows."""

import getpass
import os
from subprocess import Popen, PIPE, DEVNULL

from cylc.flow.network.resolvers import BaseResolvers
from cylc.flow.data_store_mgr import WORKFLOW


# show traceback from cylc commands
DEBUG = True


def snake_to_kebab(snake):
    """Convert snake_case text to --kebab-case text.

    Examples:
        >>> snake_to_kebab('foo_bar_baz')
        '--foo-bar-baz'
        >>> snake_to_kebab('')
        ''
        >>> snake_to_kebab(None)
        Traceback (most recent call last):
        TypeError: <class 'NoneType'>

    """
    if isinstance(snake, str):
        if not snake:
            return ''
        return f'--{snake.replace("_", "-")}'
    raise TypeError(type(snake))


def check_cylc_version(version):
    """Check the provided Cylc version is available on the CLI.

    Sets CYLC_VERSION=version and tests the result of cylc --version
    to make sure the requested version is installed and selectable via
    the CYLC_VERSION environment variable.
    """
    proc = Popen(
        ['cylc', '--version'],
        env={**os.environ, 'CYLC_VERSION': version},
        stdin=DEVNULL,
        stdout=PIPE,
        stderr=PIPE,
        text=True
    )
    ret = proc.wait(timeout=5)
    out, err = proc.communicate()
    return ret or out.strip() == version


class Services:
    """Cylc services provided by the UI Server."""

    @staticmethod
    def _error(message):
        """Format error case response."""
        return [
            False,
            str(message)
        ]

    @staticmethod
    def _return(message):
        """Format success case response."""
        return [
            True,
            message
        ]

    @classmethod
    async def play(cls, workflows, args, workflows_mgr, log):
        """Calls `cylc play`."""
        response = []

        # get ready to run the command
        try:
            # check that the request cylc version is available
            cylc_version = None
            if 'cylc_version' in args:
                cylc_version = args['cylc_version']
                if not check_cylc_version(cylc_version):
                    return cls._error(
                        f'cylc version not available: {cylc_version}'
                    )
                args = dict(args)
                args.pop('cylc_version')

            # build the command
            cmd = ['cylc', 'play', '--color=never']
            for key, value in args.items():
                if value is False:
                    # don't add binary flags
                    continue
                key = snake_to_kebab(key)
                if not isinstance(value, list):
                    value = [value]
                for item in value:
                    cmd.append(key)
                    if item is not True:
                        # don't provide values for binary flags
                        cmd.append(item)

        except Exception as exc:
            # oh noes, something went wrong, send back confirmation
            return cls._error(exc)

        # start each requested flow
        me = getpass.getuser()
        for user, flow, _ in workflows:
            try:
                if user and user != me:
                    # TODO: multi-user support
                    # forward this request to the other users UIS
                    raise ValueError(f'Cannot start flow for user: {user}')

                # add the workflow to the command
                cmd = [*cmd, flow]

                # get a representation of the command being run
                cmd_repr = ' '.join(cmd)
                if cylc_version:
                    cmd_repr = f'CYLC_VERSION={cylc_version} {cmd_repr}'
                log.info(f'$ {cmd_repr}')

                # run cylc run
                proc = Popen(
                    cmd,
                    stdin=DEVNULL,
                    stdout=PIPE,
                    stderr=PIPE,
                    text=True
                )
                ret = proc.wait(timeout=20)

                if ret:
                    # command failed
                    _, err = proc.communicate()
                    raise Exception(
                        f'Could not start {flow} - {cmd_repr}'
                        # suppress traceback unless in debug mode
                        + (f' - {err}' if DEBUG else '')
                    )

            except Exception as exc:
                # oh noes, something went wrong, send back confirmation
                return cls._error(exc)

            else:
                # send a success message
                return cls._return(
                    'Workflow started'
                )

        # trigger a re-scan
        await workflows_mgr.update()
        return response


class Resolvers(BaseResolvers):
    """UI Server context GraphQL query and mutation resolvers."""

    workflows_mgr = None

    def __init__(self, data, log, **kwargs):
        super().__init__(data)
        self.log = log

        # Set extra attributes
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    # Mutations
    async def mutator(self, info, *m_args):
        """Mutate workflow."""
        _, w_args, _ = m_args
        w_ids = [
            flow[WORKFLOW].id
            for flow in await self.get_workflows_data(w_args)]
        # Pass the request to the workflow GraphQL endpoints
        req_str, variables, _, _ = info.context.get('graphql_params')
        graphql_args = {
            'request_string': req_str,
            'variables': variables,
        }
        return self.workflows_mgr.multi_request('graphql', w_ids, graphql_args)

    async def service(self, info, *m_args):
        return await Services.play(
            m_args[1]['workflows'],
            m_args[2],
            self.workflows_mgr,
            log=self.log
        )

    async def nodes_mutator(self, info, *m_args):
        """Mutate node items of associated workflows."""
        _, _, w_args, _ = m_args
        w_ids = [
            flow[WORKFLOW].id
            for flow in await self.get_workflows_data(w_args)]
        if not w_ids:
            return 'Error: No matching Workflow'
        # Pass the multi-node request to the workflow GraphQL endpoints
        req_str, variables, _, _ = info.context.get('graphql_params')
        graphql_args = {
            'request_string': req_str,
            'variables': variables,
        }
        multi_args = {w_id: graphql_args for w_id in w_ids}
        return self.workflows_mgr.multi_request(
            'graphql', w_ids, multi_args=multi_args)
