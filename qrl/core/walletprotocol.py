# coding=utf-8
# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

import decimal
import time
from io import StringIO

import simplejson as json
from twisted.internet.protocol import Protocol, connectionDone

from pyqrllib.pyqrllib import mnemonic2bin, hstr2bin, bin2hstr
from qrl.core import helper, logger, config
from qrl.crypto.words import wordlist
from qrl.crypto.xmss import XMSS


def hexseed_to_seed(hex_seed):
    if len(hex_seed) != 96:
        return False
    return hstr2bin(hex_seed)


#FIXME: Clean this up

class WalletProtocol(Protocol):
    def __init__(self):
        self.cmd_list = ['seed', 'hexseed', 'recoverfromhexseed', 'recoverfromwords',
                         'stakenextepoch', 'stake', 'wallet', 'send', 'mempool',
                         'getnewaddress', 'quit', 'exit', 'search', 'help',
                         'savenewaddress', 'getinfo', 'blockheight', 'json_block',
                         'reboot', 'peers', 'create', 'listaddresses']

        self.output = {'status': 1,
                       'keys': [],
                       'message': StringIO()}

        self.isJSON = False

    # Parse out passed in argument to get:
    # 1. Command ([0])
    # 1. 0-Many arguments ([1:])
    def parse_cmd(self, data):

        # Get entered line as an array of strings delimited by "space."
        # Will chomp away any extra spaces
        data = data.split()
        # Arguments include anything beyond the first index

        if len(data) != 0:  # if anything was entered

            command = data[0]
            args = None
            if len(data) > 0:  # args optional
                args = data[1:]

            if command in self.cmd_list:

                # Use switch cases when porting to a different language
                if command == 'create':
                    self.factory.p2pFactory.pos.create_next_block(int(args[0]))
                    self.output['status'] = 0
                    self.output['message'].write('Creating blocknumber #'+str(args[0]))
                elif command == 'getnewaddress':
                    self.getnewaddress(args)

                elif command == 'hexseed':
                    for addr_bundle in self.factory.chain.wallet.address_bundle:
                        if isinstance(addr_bundle.xmss, XMSS):
                            self.output['status'] = 0
                            self.output['message'].write('Address: ' + addr_bundle.xmss.get_address() + '\r\n')
                            self.output['message'].write('Recovery seed: ' + addr_bundle.xmss.get_hexseed() + '\r\n')
                            self.output['keys'] += ['Address', 'Recovery seed']
                            self.output['Address'] = addr_bundle.xmss.get_address()
                            self.output['Recovery seed'] = addr_bundle.xmss.get_hexseed()

                elif command == 'seed':
                    for addr_bundle in self.factory.chain.wallet.address_bundle:
                        if isinstance(addr_bundle.xmss, XMSS):
                            self.output['status'] = 0
                            self.output['message'].write('Address: ' + addr_bundle.xmss.get_address() + '\r\n')
                            self.output['message'].write('Recovery seed: ' + addr_bundle.xmss.get_mnemonic() + '\r\n')
                            self.output['keys'] += ['Address', 'Recovery seed']

                elif command == 'search':
                    if not args:
                        self.output['status'] = 1
                        self.output['message'].write('>>> Usage: search <txhash or Q-address>\r\n')
                        return

                    tmp_output = None
                    if args[0][0] == 'Q':
                        tmp_output = json.loads(self.factory.chain.search_address(args[0]))
                        self.output['message'].write('Address: ' + str(args[0]))
                        self.output['message'].write('\r\nBalance: ' + str(tmp_output['state']['balance']))
                        self.output['message'].write('\r\nTransactions: ' + str(tmp_output['state']['transactions']))
                        for tx in tmp_output['transactions']:
                            self.output['message'].write(str(tx['txhash']))
                            self.output['message'].write(' ')
                            self.output['message'].write(str(tx['txfrom']))
                            self.output['message'].write(' ')
                            self.output['message'].write(str(tx['txto']))
                            self.output['message'].write(' ')
                            self.output['message'].write(str(tx['amount']))
                            self.output['message'].write('\r\n')
                    else:
                        tmp_output = json.loads(self.factory.chain.search_txhash(args[0]))
                        self.output['message'].write('Txnhash: ')
                        self.output['message'].write(args[0])
                        if tmp_output['status'] == 'Error':
                            self.output['message'].write('\r\n')
                            self.output['message'].write(str(tmp_output['error']))
                            self.output['message'].write('\r\n')
                            return True
                        self.output['message'].write('\r\nTimestamp: ')
                        self.output['message'].write(tmp_output['timestamp'])
                        self.output['message'].write('\r\nBlockNumber: ')
                        self.output['message'].write(tmp_output['block'])
                        self.output['message'].write('\r\nConfirmations: ')
                        self.output['message'].write(tmp_output['confirmations'])
                        self.output['message'].write('\r\nAmount: ')
                        self.output['message'].write(tmp_output['amount'])
                        self.output['message'].write('\r\n')

                    if not tmp_output:
                        self.output['status'] = 1
                        self.output['message'].write('>>> No Information available')
                        return True

                    for key in list(tmp_output.keys()):
                        self.output['keys'] += [str(key)]
                        self.output[key] = tmp_output[key]

                    self.output['status'] = 0
                    self.output['message'].write('')

                elif command == 'json_block':

                    if not args:
                        self.output['message'].write(
                            helper.json_print_telnet(self.factory.chain.m_get_last_block()) + '\r\n')
                        return True
                    try:
                        int(args[0])
                    except:
                        self.output['message'].write('>>> Try "json_block <block number>" \r\n')
                        return True

                    if int(args[0]) > self.factory.chain.m_blockheight():
                        self.output['message'].write('>>> Block > Blockheight\r\n')
                        return True
                    self.output['status'] = 0
                    self.output['message'].write(
                        helper.json_print_telnet(self.factory.chain.m_get_block(int(args[0]))) + '\r\n')

                elif command == 'savenewaddress':
                    self.savenewaddress()

                elif command == 'recoverfromhexseed':
                    if not args or not hexseed_to_seed(args[0]):
                        self.output['message'].write('>>> Usage: recoverfromhexseed <paste in hexseed>\r\n')
                        self.output['message'].write('>>> Could take up to a minute..\r\n')
                        self.output['message'].write('>>> savenewaddress if Qaddress matches expectations..\r\n')
                        return True

                    self.output['status'] = 0
                    addr = self.factory.chain.wallet.get_new_address(address_type='XMSS', seed=hexseed_to_seed(args[0]))
                    self.factory.newaddress = addr
                    self.output['message'].write('>>> Recovery address: ' + addr[1].get_address() + '\r\n')
                    self.output['message'].write('>>> Recovery seed phrase: ' + addr[1].get_mnemonic() + '\r\n')
                    self.output['message'].write('>>> hexSEED confirm: ' + addr[1].get_hexseed() + '\r\n')
                    self.output['message'].write('>>> savenewaddress if Qaddress matches expectations..\r\n')

                    self.output['keys'] += ['recovery_address', 'recovery_seed_phrase', 'hexseed_confirm']
                    self.output['recovery_address'] = addr[1].get_address()
                    self.output['recovery_seed_phrase'] = addr[1].get_mnemonic()
                    self.output['hexseed_confirm'] = addr[1].get_hexseed()

                elif command == 'recoverfromwords':
                    if not args:
                        self.output['message'].write(
                            '>>> Usage: recoverfromwords <paste in 32 mnemonic words>\r\n')
                        return True
                    self.output['message'].write('>>> trying..this could take up to a minute..\r\n')
                    if len(args) != 32:
                        self.output['message'].write(
                            '>>> Usage: recoverfromwords <paste in 32 mnemonic words>\r\n')
                        return True

                    args = ' '.join(args)
                    addr = self.factory.chain.wallet.get_new_address(address_type='XMSS', seed=mnemonic2bin(args, wordlist))
                    self.factory.newaddress = addr
                    self.output['status'] = 0
                    self.output['message'].write('>>> Recovery address: ' + addr[1].get_address() + '\r\n')
                    self.output['message'].write('>>> Recovery hexSEED: ' + addr[1].get_hexseed() + '\r\n')
                    self.output['message'].write('>>> Mnemonic confirm: ' + addr[1].get_mnemonic() + '\r\n')
                    self.output['message'].write('>>> savenewaddress if Qaddress matches expectations..\r\n')

                    self.output['keys'] += ['recovery_address', 'recovery_hexseed', 'mnemonic_confirm']
                    self.output['recovery_address'] = addr[1].get_address()
                    self.output['recovery_hexseed'] = addr[1].get_hexseed()
                    self.output['mnemonic_confirm'] = addr[1].get_mnemonic()

                elif command == 'stake':
                    self.output['status'] = 0
                    self.output['message'].write(
                        '>> Toggling stake from: ' + str(self.factory.p2pFactory.stake) + ' to: ' + str(
                            not self.factory.p2pFactory.stake) + '\r\n')

                    self.factory.p2pFactory.stake = not self.factory.p2pFactory.stake
                    logger.info(('STAKING set to: ', self.factory.p2pFactory.stake))
                    self.output['keys'] += ['stake']
                    self.output['stake'] = self.factory.p2pFactory.stake

                elif command == 'stakenextepoch':
                    self.output['status'] = 0
                    self.output['message'].write(
                        '>>> Sending a stake transaction for address: ' + self.factory.chain.mining_address + ' to activate next epoch(' + str(
                            config.dev.blocks_per_epoch - (
                                self.factory.chain.m_blockchain[-1].blockheader.blocknumber - (
                                    self.factory.chain.m_blockchain[
                                        -1].blockheader.epoch * config.dev.blocks_per_epoch))) + ' blocks time)\r\n')

                    logger.info(('STAKE for address:', self.factory.chain.mining_address))


                    blocknumber = self.factory.chain.block_chain_buffer.height() + 1
                    self.factory.p2pFactory.pos.make_st_tx(blocknumber=blocknumber, first_hash=None)

                elif command == 'send':
                    self.send_tx(args)

                elif command == 'mempool':
                    self.output['status'] = 0
                    self.output['message'].write('>>> Number of transactions in memory pool: ' + str(
                        len(self.factory.chain.transaction_pool)) + '\r\n')
                    self.output['keys'] += ['txn_nos']
                    self.output['txn_nos'] = len(self.factory.chain.transaction_pool)

                elif command == 'help':
                    self.output['status'] = 0
                    self.output['message'].write(
                        '>>> QRL ledger help: try {}'.format(', '.join(self.cmd_list)) + '\r\n') 
                # removed 'hrs, hrs_check,'
                elif command == 'quit' or command == 'exit':
                    self.transport.loseConnection()

                elif command == 'listaddresses':
                    addresses, num_sigs, types = self.factory.chain.wallet.inspect_wallet()
                    self.output['status'] = 0
                    self.output['keys'] += ['addresses']
                    self.output['addresses'] = []
                    for addr_bundle in range(len(addresses)):
                        self.output['message'].write(str(addr_bundle) + ', ' + addresses[addr_bundle] + '\r\n')
                        self.output['addresses'] += [addresses[addr_bundle]]

                elif command == 'wallet':
                    self.wallet()

                elif command == 'getinfo':
                    self.output['status'] = 0
                    self.output['message'].write('>>> Version: ' + self.factory.chain.version_number + '\r\n')
                    self.output['message'].write('>>> Uptime: ' + str(time.time() - self.factory.start_time) + '\r\n')
                    self.output['message'].write(
                        '>>> Nodes connected: ' + str(len(self.factory.p2pFactory.peer_connections)) + '\r\n')
                    self.output['message'].write('>>> Staking set to: ' + str(self.factory.p2pFactory.stake) + '\r\n')
                    self.output['message'].write('>>> Sync status: ' + self.factory.p2pFactory.nodeState.state.name + '\r\n')

                    self.output['keys'] += ['version', 'uptime', 'nodes_connected', 'staking_status', 'sync_status']
                    self.output['version'] = self.factory.chain.version_number
                    self.output['uptime'] = str(time.time() - self.factory.start_time)
                    self.output['nodes_connected'] = str(len(self.factory.p2pFactory.peer_connections))
                    self.output['staking_status'] = str(self.factory.p2pFactory.stake)
                    self.output['sync_status'] = self.factory.p2pFactory.nodeState.state.name


                elif command == 'blockheight':
                    self.output['status'] = 0
                    self.output['message'].write('>>> Blockheight: ' + str(self.factory.chain.m_blockheight()) + '\r\n')
                    self.output['message'].write(
                        '>>> Headerhash: ' + bin2hstr(self.factory.chain.m_blockchain[-1].blockheader.headerhash) + '\r\n')


                    self.output['keys'] += ['blockheight', 'headerhash']
                    self.output['blockheight'] = self.factory.chain.m_blockheight()
                    self.output['headerhash'] = bin2hstr(self.factory.chain.m_blockchain[-1].blockheader.headerhash)

                elif command == 'peers':
                    self.output['status'] = 0
                    self.output['message'].write('>>> Connected Peers:\r\n')
                    self.output['keys'] += ['peers']
                    self.output['peers'] = {}
                    for peer in self.factory.p2pFactory.peer_connections:
                        self.output['message'].write(
                            '>>> ' + peer.conn_identity + " [" + peer.version + "]  blockheight: " + str(
                                peer.blockheight) + '\r\n')
                        self.output['peers'][peer.conn_identity] = {}
                        self.output['peers'][peer.conn_identity]['version'] = peer.version
                        self.output['peers'][peer.conn_identity]['blockheight'] = peer.blockheight


                elif command == 'reboot':
                    if len(args) < 1:
                        self.output['message'].write('>>> reboot <password>\r\n')
                        self.output['message'].write('>>> or\r\n')
                        self.output['message'].write('>>> reboot <password> <nonce>\r\n')
                        self.output['message'].write('>>> or\r\n')
                        self.output['message'].write('>>> reboot <password> <nonce> <trim_blocknum>\r\n')
                        return True
                    json_hash, err = None, None
                    if len(args) == 3:
                        json_hash, status = self.factory.chain.generate_reboot_hash(args[0], args[1], args[2])
                        self.output['message'].write(str(args[0]) + str(args[1]) + str(args[2]))
                    elif len(args) == 2:
                        json_hash, status = self.factory.chain.generate_reboot_hash(args[0], args[1])
                    else:
                        json_hash, status = self.factory.chain.generate_reboot_hash(args[0])

                    if json_hash:
                        self.factory.p2pFactory.send_reboot(json_hash)
                        # self.factory.state.update(NState.synced)
                    self.output['message'].write(status)

        else:
            return False

        return True


    # Called when a command is recieved through telnet
    # Might be a good idea to use a json encrypted wallet
    def dataReceived(self, data):
        data = data.strip().decode()

        self.factory.recn += 1
        self.isJSON = False
        if data.lower().startswith('json '):
            self.isJSON = True
            data = data[5:]
        try:
            if not self.parse_cmd(data):
                self.output['status'] = 1
                self.output['message'].write(">>> Command not recognised. Use 'help' for details \r\n")
        except KeyboardInterrupt as e:
            self.output['message'] = StringIO()
            self.output['message'].write('Unexpected Error\r\nReport to QRL Developers')
            logger.error('Unexpected Error WalletProtocol\n')
            logger.exception(e)

        self.output['message'] = self.output['message'].getvalue()

        try:
            if self.isJSON:
                self.transport.write('%s' %(str(json.dumps(self.output)),))
            else:
                self.transport.write(bytes(str(self.output['message']), 'utf-8'))
        except Exception as e:
            logger.error('Walletprotocol unexpected exception while sending msg to client')
            logger.exception(e)
            pass

        del self.output
        self.output = {'status': 1,
                       'keys': [],
                       'message': StringIO()}

    # What does this do?
    # whenever you type telnet 127.0.0.1 2000
    # a connection is made and this function is called to initialize the things.
    def connectionMade(self):
        self.transport.write(b'QRL node connection established. Try starting with "help" ')
        self.factory.connections += 1
        if self.factory.connections > 1:
            logger.info('only one local connection allowed')
            self.transport.write(b'only one local connection allowed, sorry')
            self.transport.loseConnection()
        else:
            if self.transport.getPeer().host == '127.0.0.1':
                logger.info('>>> new local connection %s %s', str(self.factory.connections), self.transport.getPeer())
            else:
                self.transport.loseConnection()
                logger.info('Unauthorised remote login attempt..')

    def connectionLost(self, reason=connectionDone):
        self.factory.connections -= 1

        ###################################### LOCAL WALLET ACCESS ###############################################

    # Pseudocode:

    # is chain up to date? If not, fail/inform user
    # is address null/void? If it is, fail/print usage instructions
    # is the first letter of the address Q? If not, fail/print usage instructions
    # is the address in use? If not, fail/inform user

    # if all of these are met, return the balance
    def getbalance(self, addr):
        self.output['status'] = 1

        # is chain up to date? If not, fail/inform user
        if self.factory.state.state_uptodate(self.factory.chain.height()) is False:
            self.output['message'].write('>>> LevelDB not up to date..\r\n')
            # add "force" argument to bring it up to date and get balance?
            return

        # is address null/void? If it is, fail/print usage instructions
        if not addr:
            self.output['message'].write('>>> Usage: getbalance <address> (Addresses begin with Q)\r\n')
            return

        # is the first letter of the address Q? If not, fail/print usage instructions
        if addr[0][0] != 'Q':
            self.output['message'].write('>>> Usage: getbalance <address> (Addresses begin with Q)\r\n')
            return

        # is the address in use? If not, fail/inform user
        if self.factory.state.state_address_used(addr[0]) is False:
            self.output['message'].write(bytes('>>> Unused address: ' + addr + '\r\n', 'utf-8'))
            return

        # if all of these are met, return the balance
        self.output['status'] = 0
        balance = self.factory.state.state_balance(addr[0])
        self.output['message'].write(bytes('>>> balance:  ' + str(balance) + '\r\n', 'utf-8'))
        self.output['keys'] += ['balance']
        self.output['balance'] = balance
        return

    # Pseudocode:
    # If no arguments are used, or more than 3 are used, fail/inform user of usage
    # else:
    #	get signature type to use, reject if the type is incorrect
    #   prevent user from generating an extremely large number of XMSS signatures
    #	generate address
    #	inform user of address information
    #	tell them how to save the address to wallet file
    def getnewaddress(self, args):
        self.output['status'] = 0
        self.output['message'].write('>>> Creating new address, please be patient as this can take some time ...\r\n')
        self.output['keys'] += ['keypair_type', 'possible_signatures', 'address']

        addr_bundle = self.factory.chain.wallet.get_new_address()

        self.output['message'].write('>>> Keypair type: ' + ''.join(addr_bundle[1].get_type() + '\r\n'))
        self.output['message'].write('>>> Signatures possible with address: ' + str(addr_bundle[1].get_number_signatures()) + '\r\n')
        self.output['message'].write('>>> Address: ' + addr_bundle[1].get_address() + '\r\n')

        self.output['keypair_type'] = ''.join(addr_bundle[1].get_type() + '\r\n')
        self.output['possible_signatures'] = str(addr_bundle[1].get_number_signatures())
        self.output['address'] = addr_bundle[1].get_address()

        # TODO: Would you like to save this address to your wallet file (call savenewaddress)? Y/N
        self.output['message'].write(">>> type 'savenewaddress' to append to wallet file" + '\r\n')
        self.factory.newaddress = addr_bundle

        return

    # Simply saves wallet information
    def savenewaddress(self):
        self.output['status'] = 1
        if not self.factory.newaddress:
            self.output['message'].write(">>> No new addresses created, yet. Try 'getnewaddress'" + '\r\n')
            return
        self.output['status'] = 0
        self.factory.chain.wallet.append_wallet(self.factory.newaddress)
        self.output['message'].write('>>> new address saved in self.factory.chain.wallet.\r\n')
        return

    # This method is for sending between local wallets as well as network wallets
    def send_tx(self, args):
        self.output['status'] = 1
        # Check if method was used correctly
        if not args or len(args) < 3:
            self.output['message'].write('>>> Usage: send <from> <to> <amount> [<fee>]\r\n')
            self.output['message'].write('>>> i.e. send 0 4 100 5\r\n')
            self.output['message'].write('>>> ^ will send 100 coins from address 0 to 4 from the wallet\r\n')
            self.output['message'].write('>>> <to> can be a pasted address (starts with Q)\r\n')
            self.output['message'].write('>>> 5 is the txn fee\r\n')
            return

        wallet_from = args[0]
        wallet_to = args[1]

        # Check if the wallet entered is a local wallet (should be, since sender should be local - it's you)
        try:
            int(wallet_from)
        except:
            self.output['message'].write(
                '>>> Invalid sending address. Try a valid number from your wallet - type wallet for details.\r\n')
            return

        # Check if local wallet number is higher than the number of local wallets that are saved
        if int(wallet_from) > len(self.factory.chain.wallet.list_addresses(self.factory.chain.state, self.factory.chain.transaction_pool)) - 1:
            self.output['message'].write(
                '>>> Invalid sending address. Try a valid number from your wallet - type wallet for details.\r\n')
            return

        # perhaps make a "wallet_precondition(wallet)" method
        # to check if the wallet string is correct
        # good way to centralize that code too
        # in case it ever changes

        # if wallet_to is not a local wallet, and wallet_to is not prepended by Q and

        if len(wallet_to) > 1 and wallet_to[0] != 'Q' and self.factory.state.state_hrs(wallet_to) != False:
            pass
        elif wallet_to[0] == 'Q':
            pass
        else:
            try:
                int(wallet_to)
            except:
                self.output['message'].write(
                    '>>> Invalid receiving address - addresses must start with Q. Try a number from your self.factory.chain.wallet.\r\n')
                return
            if int(wallet_to) > len(self.factory.chain.wallet.list_addresses(self.factory.chain.state, self.factory.chain.transaction_pool)) - 1:
                self.output['message'].write(
                    '>>> Invalid receiving address - addresses must start with Q. Try a number from your self.factory.chain.wallet.\r\n')
                return
            wallet_to = int(wallet_to)

        # Check to see if sending amount > amount owned (and reject if so)
        # This is hard to interpret. Break it up?
        balance = self.factory.state.state_balance(self.factory.chain.wallet.address_bundle[int(wallet_from)].address)
        send_amt_arg = args[2]
        try:
            float(send_amt_arg)
        except:
            self.output['message'].write(
                '>>> Invalid amount type. Type a number (less than or equal to the balance of the sending address)\r\n')
            return

        amount = decimal.Decimal(decimal.Decimal(send_amt_arg) * 100000000).quantize(decimal.Decimal('1'),
                                                                                     rounding=decimal.ROUND_HALF_UP)
        fee = 0
        if len(args) == 4:
            fee = decimal.Decimal(decimal.Decimal(args[3]) * 100000000).quantize(decimal.Decimal('1'),
                                                                                 rounding=decimal.ROUND_HALF_UP)
        if balance < amount + fee:
            self.output['message'].write(
                '>>> Invalid amount to send. Type a number less than or equal to the balance of the sending address\r\n')
            return

        # Stop user from sending less than their entire balance if they've only
        # got one signature remaining.
        sigsremaining = self.factory.chain.wallet.get_num_signatures(self.factory.chain.wallet.address_bundle[int(args[0])].address)
        if sigsremaining is 1:
            if amount < balance:
                self.output['message'].write(
                    '>>> Stop! You only have one signing signature remaining. You should send your entire balance or the remainder will be lost!\r\n')
                return
        txto = args[1]
        if txto.isdigit():
            txto = int(txto)
        tx = self.factory.chain.create_my_tx(txfrom=int(args[0]), txto=txto, amount=amount, fee=fee)

        if tx is False:
            self.output['message'].write('Failed to Create txn')
            return

        if tx.validate_tx():
            block_chain_buffer = self.factory.chain.block_chain_buffer
            tx_state = block_chain_buffer.get_stxn_state(blocknumber=block_chain_buffer.height(),
                                                         addr=tx.txfrom)
            if not tx.state_validate_tx(tx_state=tx_state,
                                        transaction_pool=self.factory.chain.transaction_pool):
                self.output['message'].write('>>> OTS key reused')
                return
        else:
            self.output['message'].write('>>> TXN failed at validate_tx')
            logger.info('>>> TXN failed at validate_tx')
            return

        # send the transaction to peers (ie send it to the network - we are done)
        self.factory.p2pFactory.send_tx_to_peers(tx)
        self.output['status'] = 0
        self.output['message'].write('>>> ' + bin2hstr(tx.txhash))
        self.output['message'].write('>>> From: ' + str(tx.txfrom) + ' To: ' + str(tx.txto) + ' For: ' + str(
            tx.amount / 100000000.000000000) + ' Fee: ' + str(tx.fee / 100000000.000000000) + '\r\n')
        self.output['message'].write('>>>created and sent into p2p network\r\n')
        return

    def wallet(self):
        if not self.factory.state.state_uptodate(self.factory.chain.height()):
            self.factory.state.state_read_chain(self.factory.chain)

        self.output['status'] = 0
        self.output['message'].write('>>> Wallet contents:\r\n')
        self.output['keys'] += ['list_addresses']
        self.output['list_addresses'] = {}

        list_addr, list_addresses = self.factory.chain.wallet.list_addresses(self.factory.chain.state, self.factory.chain.transaction_pool, True)
        self.output['list_addresses'] = list_addresses

        y = 0
        for address in list_addr:
            self.output['message'].write(str(y) + str(address) + '\r\n')
            y += 1
