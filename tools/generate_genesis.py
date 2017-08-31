from __future__ import print_function

import json

import qrl.crypto.xmss

num_accounts = 100
file_name = "aws_wallet"

wallets = {}
for i in range(num_accounts):
    print("Generating (", i + 1, "/", num_accounts, ")")
    wallet = qrl.crypto.xmss.XMSS(signatures=4096, SEED=None)
    wallets[wallet.address] = wallet.mnemonic

with open(file_name, 'w') as f:
    json.dump(wallets, f)#, encoding = "ISO-8859-1")

