# Python Implementation of the Ethereum protocol

[![Join the conversation on Discord](https://img.shields.io/discord/809793915578089484?color=blue&label=chat&logo=discord&logoColor=white)](https://discord.gg/GHryRvPB84)
[![Build Status](https://circleci.com/gh/ethereum/py-evm.svg?style=shield)](https://circleci.com/gh/ethereum/py-evm)
[![PyPI version](https://badge.fury.io/py/py-evm.svg)](https://badge.fury.io/py/py-evm)
[![Python versions](https://img.shields.io/pypi/pyversions/py-evm.svg)](https://pypi.python.org/pypi/py-evm)
[![Docs build](https://readthedocs.org/projects/py-evm/badge/?version=latest)](http://py-evm.readthedocs.io/en/latest/?badge=latest)

## 核心逻辑加入了中文注释
核心目录：eth，其子目录：
*  chains：不同的区块链网络，包括主网、测试网等
*  consensus：共识协议，包括pow和pos
*  db:区块链状态存储，所有和区块链状态相关的永久存储功能
*  estimators：和gas估算相关的方法
*  precompiles：一些预编译合约
*  rlp:RLP 是 Ethereum 中用于编码和解码数据的一种编码规范，通常用于序列化和反序列化数据结构，如交易、区块头和状态树节点等
*  vm:核心类：
*   forks：不同的以太坊硬分叉（版本），第一个版本为frontier，大多数逻辑都在该版本实现，硬分叉都是继承自上一个分叉版本，若有新功能或者有功能需要更新则会重写父类的方法，forks文件夹中有如下目录：
*     blocks.py：和区块相关
*     computation.py:和交易计算执行相关（合约执行的核心功能）
*     headers.py：区块头相关信息，包括了挖矿难度等
*     opcodes：该版本支持的所有操作码
*     state.py：和交易的处理相关
*     transaction.py：交易类型，包含了签名和未签名交易，定义了交易的创建、验证等方法
*   logic：所有操作码所对应的具体操作
*   base.py：VM类，可以当作源码入口看，定义了各种和VM操作相关的方法。若只看交易处理过程可以从311行apply_all_transactions()方法开始。

## Py-EVM
Py-EVM is an implementation of the Ethereum protocol in Python. It contains the low level
primitives for the existing Ethereum 1.0 chain as well as emerging support for the upcoming
Ethereum 2.0 / Serenity spec.

### Goals

Py-EVM aims to eventually become the defacto Python implementation of the Ethereum protocol,
enabling a wide array of use cases for both public and private chains. 

In particular Py-EVM aims to:

- be a reference implementation of the Ethereum 1.0 and 2.0 implementation in one of the most widely used and understood languages, Python.

- be easy to understand and modifiable

- have clear and simple APIs

- come with solid, friendly documentation

- deliver the low level primitives to build various clients on top (including *full* and *light* clients)

- be highly flexible to support both research as well as alternate use cases like private chains.


## Quickstart

[Get started in 5 minutes](https://py-evm.readthedocs.io/en/latest/guides/quickstart.html)

## Documentation

Check out the [documentation on our official website](https://py-evm.readthedocs.io/en/latest/)

## Want to help?

Want to file a bug, contribute some code, or improve documentation? Excellent! Read up on our
guidelines for [contributing](https://py-evm.readthedocs.io/en/latest/contributing.html) and then check out one of our issues that are labeled [Good First Issue](https://github.com/ethereum/py-evm/issues?q=is%3Aissue+is%3Aopen+label%3A%22Good+First+Issue%22).
