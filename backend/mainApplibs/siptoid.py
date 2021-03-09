#! /usr/bin/env python
#! /bin/python
# -*- coding: utf-8 -*-

import json
import sys
from keycloak import KeycloakAdmin

#Sipnum = sys.argv[1]


class KeycloakAdm(object):
    def __init__(self):

        self.ClientId = "admin-cli"
        self.Realm = "zdr"
        self.Username = "atsadmin"
        self.Password = "xxxxxxxxx"
        self.FQDN = "auth.mydomain.dev"
        self.keycloak_admin = KeycloakAdmin(server_url="https://"+self.FQDN+"/auth/",
                                            username=self.Username,
                                            password=self.Password,
                                            realm_name=self.Realm,
                                            verify=True)
        self.keycloak_users = self.keycloak_admin.get_users({})
        self.client_id=self.keycloak_admin.get_client_id("crm")

    def __del__(self):
        # Get users Returns a list of users, filtered according to query parameters
        users = self.keycloak_admin.get_users({})
        for user in users:
            if user['username'] == self.Username:
                my_sessions = self.keycloak_admin.get_sessions(user['id'])
                for my_session in my_sessions:
                    ret = self.keycloak_admin.connection.raw_delete(
                        "admin/realms/"+self.Realm+"/sessions/"+my_session['id'])

    def GetLastUserSessionTime(self, user):
        lastAccess = None
        sessions = self.keycloak_admin.get_sessions(user['id'])
                             
        for sess in sessions:
            sestime = int(sess['lastAccess'])
            if lastAccess is not None:
                if lastAccess < sestime:
                    lastAccess = sestime
            else:
                lastAccess = sestime
        return lastAccess

    def GetIdBySip(self, sip):
        lastAccess = None
        retId = None

        for user in self.keycloak_users:
            if 'attributes' in user:
                attributes = user['attributes']
                if 'sip' in attributes:
                    if str(attributes['sip'][0]) == str(sip):
                        if self.GetClientRoleOfUserByRoleName('dev', user['id']) == None:
                            
                            sestime = self.GetLastUserSessionTime(user)
                            if lastAccess is not None:
                                if lastAccess < sestime:
                                    lastAccess = sestime
                                    retId = user['id']
                            else:
                                lastAccess = sestime
                                retId = user['id']
        return retId

    def GetNameBySip(self, sip):
        lastAccess = None
        ret = None
        for user in self.keycloak_users:
            if 'attributes' in user:
                attributes = user['attributes']
                if 'sip' in attributes:
                    if str(attributes['sip'][0]) == str(sip):
                        if self.GetClientRoleOfUserByRoleName('dev', user['id']) == None:

                            sestime = self.GetLastUserSessionTime(user)
                            if lastAccess is not None:
                                if lastAccess < sestime:
                                    lastAccess = sestime
                                    ret = user['username']
                            else:
                                lastAccess = sestime
                                ret = user['username']
        return ret

    def GetClientRoleOfUserByRoleName(self, roleName, userID):
        rolesOfUser = self.keycloak_admin.get_client_roles_of_user(user_id=userID, client_id=self.client_id)
        #keycloak_admin.get_available_client_roles_of_user(user_id="user_id", client_id="client_id")
        #print user['username'], user['id']

        for role in rolesOfUser:
            if roleName == role['name']: return role
        return None
#kAdmin = KeycloakAdm()
#kcID = kAdmin.GetIdBySip(Sipnum)
#print kcID
