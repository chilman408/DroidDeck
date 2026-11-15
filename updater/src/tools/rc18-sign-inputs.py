#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Reconstruct an unsigned RC18 candidate before signing custody is opened.

The trusted public compiler reconstructs the frozen RC17 templates, independently
authenticated capture payload and preserving-handoff template. Never import
candidate code.
"""
import base64,importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('successor_public_preparation',Path(__file__).with_name('rc18-runtime-preparation.py'))
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)

def review(bundle,statement,predecessor,key):
 if key!=P.KEY:raise ValueError('Original publisher authority required')
 value=json.loads(statement)
 if type(value) is not dict:raise ValueError('Closed aggregate statement required')
 prefix='droidmate-rc18-release/'
 outer=P.members(bundle);parts=P.members(outer[prefix+'components.tar']);cp='droidmate-rc18-components/assets/'
 source=prefix+'source/runtime/portable/'
 activation=parts[cp+'capture-package.tar'];component=P.members(activation)['droiddeck-capture-activation/component.tar']
 expected,expected_statement,receipt=P.prepare(predecessor,component,activation,parts[cp+'capture-statement.json'],parts[cp+'capture-signature.bin'],outer[source+'capture-component-staging.statement.json'],outer[source+'capture-component-staging.sig'],value.get('sourceRevision'))
 if bundle!=expected or statement!=expected_statement:raise ValueError('Every successor byte must equal the independently reconstructed package')
 return {'kind':'droidmate-successor-signing-inputs','keySha256':key,'statementSha256':P.sha(statement),'packageSha256':P.sha(bundle),'predecessorSha256':P.PREDECESSOR,'servicesChanged':False,'installable':False}

def unique(rows):
 out={}
 for key,value in rows:
  if key in out:raise ValueError('Duplicate public signing field')
  out[key]=value
 return out

def main():
 raw=sys.stdin.buffer.read(144*1024**2+1)
 if len(raw)>144*1024**2:raise ValueError('Bounded public successor request required')
 value=json.loads(raw,object_pairs_hook=unique)
 if type(value) is not dict or set(value)!={'bundle','statement','predecessor','keySha256'}:raise ValueError('Closed successor signing request required')
 bundle,statement,predecessor=[base64.b64decode(value[name],validate=True) for name in ['bundle','statement','predecessor']]
 if len(bundle)>50*1024**2 or len(predecessor)>50*1024**2 or len(statement)>8192:raise ValueError('Bounded public inputs required')
 print(P.encode(review(bundle,statement,predecessor,value['keySha256'])).decode().strip())
if __name__=='__main__':
 try:main()
 except Exception as error:
  print('Successor signing inputs refused: '+type(error).__name__,file=sys.stderr);sys.exit(1)
