# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import glob
import numpy as np
import os
import random
import signal
import site
import subprocess
import sys
import threading
import time
import unittest

import plasma
import pyarrow as pa

DEFAULT_PLASMA_STORE_MEMORY = 10 ** 9

USE_VALGRIND = False

def random_name():
  return str(random.randint(0, 99999999))

def random_object_id():
    return plasma.ObjectID(np.random.bytes(20))

def generate_metadata(length):
  metadata_buffer = bytearray(length)
  if length > 0:
    metadata_buffer[0] = random.randint(0, 255)
    metadata_buffer[-1] = random.randint(0, 255)
    for _ in range(100):
      metadata_buffer[random.randint(0, length - 1)] = random.randint(0, 255)
  return metadata_buffer

def write_to_data_buffer(buff, length):
  array = np.frombuffer(buff, dtype="uint8")
  if length > 0:
    array[0] = random.randint(0, 255)
    array[-1] = random.randint(0, 255)
    for _ in range(100):
      array[random.randint(0, length - 1)] = random.randint(0, 255)

def create_object_with_id(client, object_id, data_size, metadata_size,
                          seal=True):
  metadata = generate_metadata(metadata_size)
  memory_buffer = client.create(object_id, data_size, metadata)
  write_to_data_buffer(memory_buffer, data_size)
  if seal:
    client.seal(object_id)
  return memory_buffer, metadata

def create_object(client, data_size, metadata_size, seal=True):
  object_id = random_object_id()
  memory_buffer, metadata = create_object_with_id(client, object_id, data_size,
                                                  metadata_size, seal=seal)
  return object_id, memory_buffer, metadata

def assert_get_object_equal(unit_test, client1, client2, object_id,
                            memory_buffer=None, metadata=None):
  client1_buff = client1.get([object_id])[0]
  client2_buff = client2.get([object_id])[0]
  client1_metadata = client1.get_metadata([object_id])[0]
  client2_metadata = client2.get_metadata([object_id])[0]
  unit_test.assertEqual(len(client1_buff), len(client2_buff))
  unit_test.assertEqual(len(client1_metadata), len(client2_metadata))
  # Check that the buffers from the two clients are the same.
  unit_test.assertTrue(plasma.buffers_equal(client1_buff, client2_buff))
  # Check that the metadata buffers from the two clients are the same.
  unit_test.assertTrue(plasma.buffers_equal(client1_metadata,
                                            client2_metadata))
  # If a reference buffer was provided, check that it is the same as well.
  if memory_buffer is not None:
    unit_test.assertTrue(plasma.buffers_equal(memory_buffer, client1_buff))
  # If reference metadata was provided, check that it is the same as well.
  if metadata is not None:
    unit_test.assertTrue(plasma.buffers_equal(metadata, client1_metadata))

def start_plasma_store(plasma_store_memory=DEFAULT_PLASMA_STORE_MEMORY,
                       use_valgrind=False, use_profiler=False,
                       stdout_file=None, stderr_file=None):
  """Start a plasma store process.
  Args:
    use_valgrind (bool): True if the plasma store should be started inside of
      valgrind. If this is True, use_profiler must be False.
    use_profiler (bool): True if the plasma store should be started inside a
      profiler. If this is True, use_valgrind must be False.
    stdout_file: A file handle opened for writing to redirect stdout to. If no
      redirection should happen, then this should be None.
    stderr_file: A file handle opened for writing to redirect stderr to. If no
      redirection should happen, then this should be None.
  Return:
    A tuple of the name of the plasma store socket and the process ID of the
      plasma store process.
  """
  if use_valgrind and use_profiler:
    raise Exception("Cannot use valgrind and profiler at the same time.")
  module_dir = site.getsitepackages()
  [plasma_dir] = glob.glob(os.path.join(module_dir[0], "plasma*"))
  plasma_store_executable = os.path.join(os.path.abspath(plasma_dir), "plasma/plasma_store")
  plasma_store_name = "/tmp/plasma_store{}".format(random_name())
  command = [plasma_store_executable,
             "-s", plasma_store_name,
             "-m", str(plasma_store_memory)]
  if use_valgrind:
    pid = subprocess.Popen(["valgrind",
                            "--track-origins=yes",
                            "--leak-check=full",
                            "--show-leak-kinds=all",
                            "--error-exitcode=1"] + command,
                           stdout=stdout_file, stderr=stderr_file)
    time.sleep(1.0)
  elif use_profiler:
    pid = subprocess.Popen(["valgrind", "--tool=callgrind"] + command,
                           stdout=stdout_file, stderr=stderr_file)
    time.sleep(1.0)
  else:
    pid = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
    time.sleep(0.1)
  return plasma_store_name, pid

class TestPlasmaClient(unittest.TestCase):

  def setUp(self):
    # Start Plasma store.
    plasma_store_name, self.p = start_plasma_store(
        use_valgrind=USE_VALGRIND)
    # Connect to Plasma.
    self.plasma_client = plasma.PlasmaClient()
    self.plasma_client.connect(plasma_store_name, "", 64)
    # For the eviction test
    self.plasma_client2 = plasma.PlasmaClient()
    self.plasma_client2.connect(plasma_store_name, "", 0)

  def tearDown(self):
    # Check that the Plasma store is still alive.
    self.assertEqual(self.p.poll(), None)
    # Kill the plasma store process.
    if USE_VALGRIND:
      self.p.send_signal(signal.SIGTERM)
      self.p.wait()
      if self.p.returncode != 0:
        os._exit(-1)
    else:
      self.p.kill()

  def test_create(self):
    # Create an object id string.
    object_id = random_object_id()
    # Create a new buffer and write to it.
    length = 50
    memory_buffer = np.frombuffer(self.plasma_client.create(object_id, length), dtype="uint8")
    for i in range(length):
      memory_buffer[i] = i % 256
    # Seal the object.
    self.plasma_client.seal(object_id)
    # Get the object.
    memory_buffer = np.frombuffer(self.plasma_client.get([object_id])[0], dtype="uint8")
    for i in range(length):
      self.assertEqual(memory_buffer[i], i % 256)

  def test_create_with_metadata(self):
    for length in range(1000):
      # Create an object id string.
      object_id = random_object_id()
      # Create a random metadata string.
      metadata = generate_metadata(length)
      # Create a new buffer and write to it.
      memory_buffer = np.frombuffer(self.plasma_client.create(object_id, length, metadata), dtype="uint8")
      for i in range(length):
        memory_buffer[i] = i % 256
      # Seal the object.
      self.plasma_client.seal(object_id)
      # Get the object.
      memory_buffer = np.frombuffer(self.plasma_client.get([object_id])[0], dtype="uint8")
      for i in range(length):
        self.assertEqual(memory_buffer[i], i % 256)
      # Get the metadata.
      metadata_buffer = np.frombuffer(self.plasma_client.get_metadata([object_id])[0], dtype="uint8")
      self.assertEqual(len(metadata), len(metadata_buffer))
      for i in range(len(metadata)):
        self.assertEqual(metadata[i], metadata_buffer[i])

  def test_create_existing(self):
    # This test is partially used to test the code path in which we create an
    # object with an ID that already exists
    length = 100
    for _ in range(1000):
      object_id = random_object_id()
      self.plasma_client.create(object_id, length, generate_metadata(length))
      try:
        self.plasma_client.create(object_id, length, generate_metadata(length))
      # TODO(pcm): Introduce a more specific error type here
      except pa.lib.ArrowException as e:
        pass
      else:
        self.assertTrue(False)

  def test_get(self):
    num_object_ids = 100
    # Test timing out of get with various timeouts.
    for timeout in [0, 10, 100, 1000]:
      object_ids = [random_object_id() for _ in range(num_object_ids)]
      results = self.plasma_client.get(object_ids, timeout_ms=timeout)
      self.assertEqual(results, num_object_ids * [None])

    data_buffers = []
    metadata_buffers = []
    for i in range(num_object_ids):
      if i % 2 == 0:
        data_buffer, metadata_buffer = create_object_with_id(
            self.plasma_client, object_ids[i], 2000, 2000)
        data_buffers.append(data_buffer)
        metadata_buffers.append(metadata_buffer)

    # Test timing out from some but not all get calls with various timeouts.
    for timeout in [0, 10, 100, 1000]:
      data_results = self.plasma_client.get(object_ids, timeout_ms=timeout)
      # metadata_results = self.plasma_client.get_metadata(object_ids,
      #                                                    timeout_ms=timeout)
      for i in range(num_object_ids):
        if i % 2 == 0:
          array1 = np.frombuffer(data_buffers[i // 2], dtype="uint8")
          array2 = np.frombuffer(data_results[i], dtype="uint8")
          np.testing.assert_equal(array1, array2)
          # TODO(rkn): We should compare the metadata as well. But currently
          # the types are different (e.g., memoryview versus bytearray).
          # self.assertTrue(plasma.buffers_equal(metadata_buffers[i // 2],
          #                                      metadata_results[i]))
        else:
          self.assertIsNone(results[i])

  def test_pickle_object_ids(self):
    # This can be used for sharing object IDs between processes.
    import pickle
    object_id = random_object_id()
    data = pickle.dumps(object_id)
    object_id2 = pickle.loads(data)
    self.assertEqual(object_id, object_id2)

  def test_store_full(self):
    # The store is started with 1GB, so make sure that create throws an
    # exception when it is full.
    def assert_create_raises_plasma_full(unit_test, size):
      partial_size = np.random.randint(size)
      try:
        _, memory_buffer, _ = create_object(unit_test.plasma_client,
                                            partial_size,
                                            size - partial_size)
      # TODO(pcm): More specific error here.
      except pa.lib.ArrowException as e:
        pass
      else:
        # For some reason the above didn't throw an exception, so fail.
        unit_test.assertTrue(False)

    # Create a list to keep some of the buffers in scope.
    memory_buffers = []
    _, memory_buffer, _ = create_object(self.plasma_client, 5 * 10 ** 8, 0)
    memory_buffers.append(memory_buffer)
    # Remaining space is 5 * 10 ** 8. Make sure that we can't create an object
    # of size 5 * 10 ** 8 + 1, but we can create one of size 2 * 10 ** 8.
    assert_create_raises_plasma_full(self, 5 * 10 ** 8 + 1)
    _, memory_buffer, _ = create_object(self.plasma_client, 2 * 10 ** 8, 0)
    del memory_buffer
    _, memory_buffer, _ = create_object(self.plasma_client, 2 * 10 ** 8, 0)
    del memory_buffer
    assert_create_raises_plasma_full(self, 5 * 10 ** 8 + 1)

    _, memory_buffer, _ = create_object(self.plasma_client, 2 * 10 ** 8, 0)
    memory_buffers.append(memory_buffer)
    # Remaining space is 3 * 10 ** 8.
    assert_create_raises_plasma_full(self, 3 * 10 ** 8 + 1)

    _, memory_buffer, _ = create_object(self.plasma_client, 10 ** 8, 0)
    memory_buffers.append(memory_buffer)
    # Remaining space is 2 * 10 ** 8.
    assert_create_raises_plasma_full(self, 2 * 10 ** 8 + 1)

  def test_contains(self):
    fake_object_ids = [random_object_id() for _ in range(100)]
    real_object_ids = [random_object_id() for _ in range(100)]
    for object_id in real_object_ids:
      self.assertFalse(self.plasma_client.contains(object_id))
      self.plasma_client.create(object_id, 100)
      self.plasma_client.seal(object_id)
      self.assertTrue(self.plasma_client.contains(object_id))
    for object_id in fake_object_ids:
      self.assertFalse(self.plasma_client.contains(object_id))
    for object_id in real_object_ids:
      self.assertTrue(self.plasma_client.contains(object_id))

  def test_hash(self):
    # Check the hash of an object that doesn't exist.
    object_id1 = random_object_id()
    try:
      self.plasma_client.hash(object_id1)
      # TODO(pcm): Introduce a more specific error type here
    except pa.lib.ArrowException as e:
      pass
    else:
      self.assertTrue(False)

    length = 1000
    # Create a random object, and check that the hash function always returns
    # the same value.
    metadata = generate_metadata(length)
    memory_buffer = np.frombuffer(self.plasma_client.create(object_id1, length, metadata), dtype="uint8")
    for i in range(length):
      memory_buffer[i] = i % 256
    self.plasma_client.seal(object_id1)
    self.assertEqual(self.plasma_client.hash(object_id1),
                     self.plasma_client.hash(object_id1))

    # Create a second object with the same value as the first, and check that
    # their hashes are equal.
    object_id2 = random_object_id()
    memory_buffer = np.frombuffer(self.plasma_client.create(object_id2, length, metadata), dtype="uint8")
    for i in range(length):
      memory_buffer[i] = i % 256
    self.plasma_client.seal(object_id2)
    self.assertEqual(self.plasma_client.hash(object_id1),
                     self.plasma_client.hash(object_id2))

    # Create a third object with a different value from the first two, and
    # check that its hash is different.
    object_id3 = random_object_id()
    metadata = generate_metadata(length)
    memory_buffer = np.frombuffer(self.plasma_client.create(object_id3, length, metadata), dtype="uint8")
    for i in range(length):
      memory_buffer[i] = (i + 1) % 256
    self.plasma_client.seal(object_id3)
    self.assertNotEqual(self.plasma_client.hash(object_id1),
                        self.plasma_client.hash(object_id3))

    # Create a fourth object with the same value as the third, but different
    # metadata. Check that its hash is different from any of the previous
    # three.
    object_id4 = random_object_id()
    metadata4 = generate_metadata(length)
    memory_buffer = np.frombuffer(self.plasma_client.create(object_id4, length, metadata4), dtype="uint8")
    for i in range(length):
      memory_buffer[i] = (i + 1) % 256
    self.plasma_client.seal(object_id4)
    self.assertNotEqual(self.plasma_client.hash(object_id1),
                        self.plasma_client.hash(object_id4))
    self.assertNotEqual(self.plasma_client.hash(object_id3),
                        self.plasma_client.hash(object_id4))

  def test_many_hashes(self):
    hashes = []
    length = 2 ** 10

    for i in range(256):
      object_id = random_object_id()
      memory_buffer = np.frombuffer(self.plasma_client.create(object_id, length), dtype="uint8")
      for j in range(length):
        memory_buffer[j] = i
      self.plasma_client.seal(object_id)
      hashes.append(self.plasma_client.hash(object_id))

    # Create objects of varying length. Each pair has two bits different.
    for i in range(length):
      object_id = random_object_id()
      memory_buffer = np.frombuffer(self.plasma_client.create(object_id, length), dtype="uint8")
      for j in range(length):
        memory_buffer[j] = 0
      memory_buffer[i] = 1
      self.plasma_client.seal(object_id)
      hashes.append(self.plasma_client.hash(object_id))

    # Create objects of varying length, all with value 0.
    for i in range(length):
      object_id = random_object_id()
      memory_buffer = np.frombuffer(self.plasma_client.create(object_id, i), dtype="uint8")
      for j in range(i):
        memory_buffer[j] = 0
      self.plasma_client.seal(object_id)
      hashes.append(self.plasma_client.hash(object_id))

    # Check that all hashes were unique.
    self.assertEqual(len(set(hashes)), 256 + length + length)

  # def test_individual_delete(self):
  #   length = 100
  #   # Create an object id string.
  #   object_id = random_object_id()
  #   # Create a random metadata string.
  #   metadata = generate_metadata(100)
  #   # Create a new buffer and write to it.
  #   memory_buffer = self.plasma_client.create(object_id, length, metadata)
  #   for i in range(length):
  #     memory_buffer[i] = chr(i % 256)
  #   # Seal the object.
  #   self.plasma_client.seal(object_id)
  #   # Check that the object is present.
  #   self.assertTrue(self.plasma_client.contains(object_id))
  #   # Delete the object.
  #   self.plasma_client.delete(object_id)
  #   # Make sure the object is no longer present.
  #   self.assertFalse(self.plasma_client.contains(object_id))
  #
  # def test_delete(self):
  #   # Create some objects.
  #   object_ids = [random_object_id() for _ in range(100)]
  #   for object_id in object_ids:
  #     length = 100
  #     # Create a random metadata string.
  #     metadata = generate_metadata(100)
  #     # Create a new buffer and write to it.
  #     memory_buffer = self.plasma_client.create(object_id, length, metadata)
  #     for i in range(length):
  #       memory_buffer[i] = chr(i % 256)
  #     # Seal the object.
  #     self.plasma_client.seal(object_id)
  #     # Check that the object is present.
  #     self.assertTrue(self.plasma_client.contains(object_id))
  #
  #   # Delete the objects and make sure they are no longer present.
  #   for object_id in object_ids:
  #     # Delete the object.
  #     self.plasma_client.delete(object_id)
  #     # Make sure the object is no longer present.
  #     self.assertFalse(self.plasma_client.contains(object_id))

  def test_illegal_functionality(self):
    # Create an object id string.
    object_id = random_object_id()
    # Create a new buffer and write to it.
    length = 1000
    memory_buffer = self.plasma_client.create(object_id, length)
    # Make sure we cannot access memory out of bounds.
    self.assertRaises(Exception, lambda: memory_buffer[length])
    # Seal the object.
    self.plasma_client.seal(object_id)
    # This test is commented out because it currently fails.
    # # Make sure the object is ready only now.
    # def illegal_assignment():
    #   memory_buffer[0] = chr(0)
    # self.assertRaises(Exception, illegal_assignment)
    # Get the object.
    memory_buffer = self.plasma_client.get([object_id])[0]

    # Make sure the object is read only.
    def illegal_assignment():
      memory_buffer[0] = chr(0)
    self.assertRaises(Exception, illegal_assignment)

  def test_evict(self):
    client = self.plasma_client2
    object_id1 = random_object_id()
    b1 = client.create(object_id1, 1000)
    client.seal(object_id1)
    del b1
    self.assertEqual(client.evict(1), 1000)

    object_id2 = random_object_id()
    object_id3 = random_object_id()
    b2 = client.create(object_id2, 999)
    b3 = client.create(object_id3, 998)
    client.seal(object_id3)
    del b3
    self.assertEqual(client.evict(1000), 998)

    object_id4 = random_object_id()
    b4 = client.create(object_id4, 997)
    client.seal(object_id4)
    del b4
    client.seal(object_id2)
    del b2
    self.assertEqual(client.evict(1), 997)
    self.assertEqual(client.evict(1), 999)

    object_id5 = random_object_id()
    object_id6 = random_object_id()
    object_id7 = random_object_id()
    b5 = client.create(object_id5, 996)
    b6 = client.create(object_id6, 995)
    b7 = client.create(object_id7, 994)
    client.seal(object_id5)
    client.seal(object_id6)
    client.seal(object_id7)
    del b5
    del b6
    del b7
    self.assertEqual(client.evict(2000), 996 + 995 + 994)

  def test_subscribe(self):
    # Subscribe to notifications from the Plasma Store.
    self.plasma_client.subscribe()
    for i in [1, 10, 100, 1000, 10000, 100000]:
      object_ids = [random_object_id() for _ in range(i)]
      metadata_sizes = [np.random.randint(1000) for _ in range(i)]
      data_sizes = [np.random.randint(1000) for _ in range(i)]
      for j in range(i):
        self.plasma_client.create(
            object_ids[j], data_sizes[j],
            metadata=bytearray(np.random.bytes(metadata_sizes[j])))
        self.plasma_client.seal(object_ids[j])
      # Check that we received notifications for all of the objects.
      for j in range(i):
        notification_info = self.plasma_client.get_next_notification()
        recv_objid, recv_dsize, recv_msize = notification_info
        self.assertEqual(object_ids[j], recv_objid)
        self.assertEqual(data_sizes[j], recv_dsize)
        self.assertEqual(metadata_sizes[j], recv_msize)

  def test_subscribe_deletions(self):
    # Subscribe to notifications from the Plasma Store. We use plasma_client2
    # to make sure that all used objects will get evicted properly.
    self.plasma_client2.subscribe()
    for i in [1, 10, 100, 1000, 10000, 100000]:
      object_ids = [random_object_id() for _ in range(i)]
      # Add 1 to the sizes to make sure we have nonzero object sizes.
      metadata_sizes = [np.random.randint(1000) + 1 for _ in range(i)]
      data_sizes = [np.random.randint(1000) + 1 for _ in range(i)]
      for j in range(i):
        x = self.plasma_client2.create(
            object_ids[j], data_sizes[j],
            metadata=bytearray(np.random.bytes(metadata_sizes[j])))
        self.plasma_client2.seal(object_ids[j])
      del x
      # Check that we received notifications for creating all of the objects.
      for j in range(i):
        notification_info = self.plasma_client2.get_next_notification()
        recv_objid, recv_dsize, recv_msize = notification_info
        self.assertEqual(object_ids[j], recv_objid)
        self.assertEqual(data_sizes[j], recv_dsize)
        self.assertEqual(metadata_sizes[j], recv_msize)

      # Check that we receive notifications for deleting all objects, as we
      # evict them.
      for j in range(i):
        self.assertEqual(self.plasma_client2.evict(1),
                         data_sizes[j] + metadata_sizes[j])
        notification_info = self.plasma_client2.get_next_notification()
        recv_objid, recv_dsize, recv_msize = notification_info
        self.assertEqual(object_ids[j], recv_objid)
        self.assertEqual(-1, recv_dsize)
        self.assertEqual(-1, recv_msize)

    # Test multiple deletion notifications. The first 9 object IDs have size 0,
    # and the last has a nonzero size. When Plasma evicts 1 byte, it will evict
    # all objects, so we should receive deletion notifications for each.
    num_object_ids = 10
    object_ids = [random_object_id() for _ in range(num_object_ids)]
    metadata_sizes = [0] * (num_object_ids - 1)
    data_sizes = [0] * (num_object_ids - 1)
    metadata_sizes.append(np.random.randint(1000))
    data_sizes.append(np.random.randint(1000))
    for i in range(num_object_ids):
      x = self.plasma_client2.create(
          object_ids[i], data_sizes[i],
          metadata=bytearray(np.random.bytes(metadata_sizes[i])))
      self.plasma_client2.seal(object_ids[i])
    del x
    for i in range(num_object_ids):
      notification_info = self.plasma_client2.get_next_notification()
      recv_objid, recv_dsize, recv_msize = notification_info
      self.assertEqual(object_ids[i], recv_objid)
      self.assertEqual(data_sizes[i], recv_dsize)
      self.assertEqual(metadata_sizes[i], recv_msize)
    self.assertEqual(self.plasma_client2.evict(1),
                     data_sizes[-1] + metadata_sizes[-1])
    for i in range(num_object_ids):
      notification_info = self.plasma_client2.get_next_notification()
      recv_objid, recv_dsize, recv_msize = notification_info
      self.assertEqual(object_ids[i], recv_objid)
      self.assertEqual(-1, recv_dsize)
      self.assertEqual(-1, recv_msize)

if __name__ == "__main__":
  if len(sys.argv) > 1:
    # Pop the argument so we don't mess with unittest's own argument parser.
    if sys.argv[-1] == "valgrind":
      arg = sys.argv.pop()
      USE_VALGRIND = True
      print("Using valgrind for tests")
  unittest.main(verbosity=2)
