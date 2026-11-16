import unittest,time
from fastapi import FastAPI,Header
from fastapi.testclient import TestClient
from sonora.google_auth import install_google_auth
from test_api import FakeAtomicGuardStore

ORIGIN='https://studio.example'
class GoogleAuthTests(unittest.TestCase):
 def setUp(self):
  self.store=FakeAtomicGuardStore();self.now=100000;self.claims={}
  app=FastAPI()
  @app.get('/api/library')
  def library(x_portal_password:str|None=Header(default=None)):
   return {'trusted':x_portal_password=='fixture-internal-only'}
  @app.post('/api/generate')
  def generation():return {'admitted':True}
  install_google_auth(app,client_id='fixture-client',origin=ORIGIN,owner_email='owner@gmail.com',password='fixture-internal-only',store=self.store,clock=lambda:self.now,verifier=lambda token:self.claims)
  self.c=TestClient(app,base_url=ORIGIN)
 def tearDown(self):self.c.close()
 def prepare(self,email='owner@gmail.com',sub='owner-subject'):
  d=self.c.get('/api/auth/config').json()
  self.claims={'aud':'fixture-client','iss':'https://accounts.google.com','exp':self.now+3600,'sub':sub,'email':email,'email_verified':True,'nonce':d['nonce']}
 def login(self,origin=ORIGIN):return self.c.post('/api/auth/google',json={'credential':'fixture-token'},headers={'Origin':origin})
 def test_anonymous_cannot_use_legacy_password_or_generate(self):
  self.assertEqual(self.c.get('/api/library',headers={'X-Portal-Password':'fixture-internal-only'}).status_code,401)
  self.assertEqual(self.c.post('/api/generate',headers={'Origin':ORIGIN}).status_code,401)
 def test_owner_gets_secure_session_and_internal_auth_only(self):
  self.prepare();r=self.login();self.assertEqual(r.status_code,200);self.assertEqual(r.json()['role'],'owner')
  cookies=r.headers.get_list('set-cookie');self.assertTrue(any('HttpOnly' in x and 'Secure' in x and 'SameSite=lax' in x for x in cookies))
  self.assertTrue(self.c.get('/api/library').json()['trusted'])
  self.assertEqual(self.c.post('/api/generate',headers={'Origin':ORIGIN}).status_code,200)
  self.assertNotIn('fixture-internal-only',r.text)
 def test_visitor_can_access_shared_generation(self):
  self.prepare('visitor@gmail.com','visitor-sub');self.assertEqual(self.login().json()['role'],'visitor')
  self.assertTrue(self.c.get('/api/auth/me').json()['authenticated'])
  self.assertEqual(self.c.get('/api/library').status_code,200)
  self.assertEqual(self.c.post('/api/generate',headers={'Origin':ORIGIN}).status_code,200)
  self.assertNotIn('owner-sub',self.store.data)
 def test_bad_claims_fail_closed(self):
  for key,value in [('aud','other-client'),('iss','https://evil.example'),('exp',0),('sub',''),('email_verified',False),('nonce','different')]:
   self.prepare();self.claims[key]=value
   self.assertEqual(self.login().status_code,401,key)
 def test_csrf_origin_required_for_login(self):
  self.prepare();self.assertEqual(self.login('https://evil.example').status_code,403)
  self.assertEqual(self.c.post('/api/auth/google',json={'credential':'x'}).status_code,403)
 def test_login_nonce_required_and_not_replayable(self):
  self.prepare();cookie=self.c.cookies.get('__Host-sonora_login');self.assertEqual(self.login().status_code,200)
  self.c.cookies.set('__Host-sonora_login',cookie,domain='studio.example',path='/')
  self.assertEqual(self.login().status_code,401)
 def test_expired_nonce_rejected(self):
  self.prepare();self.now+=301;self.assertEqual(self.login().status_code,401)
 def test_tampered_nonce_rejected(self):
  self.prepare();self.c.cookies.set('__Host-sonora_login','invalid',domain='studio.example',path='/');self.assertEqual(self.login().status_code,401)
 def test_expired_session_cannot_read_or_generate(self):
  self.prepare();self.login();self.now+=28801
  self.assertFalse(self.c.get('/api/auth/me').json()['authenticated'])
  self.assertEqual(self.c.get('/api/library').status_code,401)
 def test_owner_binding_never_reassigned(self):
  self.prepare();self.login();self.c.cookies.clear()
  self.prepare(sub='different-sub-same-email');self.assertEqual(self.login().json()['role'],'visitor')
  self.assertEqual(self.store.get('owner-sub')['sub'],'owner-subject')
 def test_cross_origin_post_denied_for_owner(self):
  self.prepare();self.login()
  self.assertEqual(self.c.post('/api/generate',headers={'Origin':'https://evil.example'}).status_code,403)
  self.assertEqual(self.c.post('/api/generate').status_code,403)
 def test_logout_invalidates_session_and_rejects_csrf(self):
  self.prepare();self.login();old=self.c.cookies.get('__Host-sonora_session')
  self.assertEqual(self.c.post('/api/auth/logout',headers={'Origin':'https://evil.example'}).status_code,403)
  self.assertEqual(self.c.post('/api/auth/logout',headers={'Origin':ORIGIN}).status_code,200)
  self.c.cookies.set('__Host-sonora_session',old,domain='studio.example',path='/')
  self.assertFalse(self.c.get('/api/auth/me').json()['authenticated'])
