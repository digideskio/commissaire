# Copyright (C) 2016  Red Hat, Inc
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

@upgrade
@cluster
@clusterexec
Feature: Upgrading Clusters

  @anonymous
  Scenario: Initiate cluster upgrade without authentication
     Given we are anonymous
       and we have an unmanaged cluster named honeynut
      when we initiate an upgrade of cluster honeynut
      then commissaire will deny access

  @slow
  Scenario: Initiate cluster upgrade with authentication
     Given we have a valid username and password
       and we have an unmanaged cluster named honeynut
      when we initiate an upgrade of cluster honeynut
      then commissaire will allow access
       and commissaire will note creation
       and commissaire will provide upgrade status
       and the provided status is in_process
