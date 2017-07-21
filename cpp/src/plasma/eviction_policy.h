// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
//   http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an
// "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
// KIND, either express or implied.  See the License for the
// specific language governing permissions and limitations
// under the License.

#ifndef PLASMA_EVICTION_POLICY_H
#define PLASMA_EVICTION_POLICY_H

#include <list>
#include <unordered_map>
#include <utility>
#include <vector>

#include "plasma/common.h"
#include "plasma/plasma.h"

namespace plasma {

// ==== The eviction policy ====
//
// This file contains declaration for all functions and data structures that
// need to be provided if you want to implement a new eviction algorithm for the
// Plasma store.

class LRUCache {
 public:
  LRUCache() {}

  void add(const ObjectID& key, int64_t size);

  void remove(const ObjectID& key);

  int64_t choose_objects_to_evict(
      int64_t num_bytes_required, std::vector<ObjectID>* objects_to_evict);

 private:
  /// A doubly-linked list containing the items in the cache and
  /// their sizes in LRU order.
  typedef std::list<std::pair<ObjectID, int64_t>> ItemList;
  ItemList item_list_;
  /// A hash table mapping the object ID of an object in the cache to its
  /// location in the doubly linked list item_list_.
  std::unordered_map<ObjectID, ItemList::iterator, UniqueIDHasher> item_map_;
};

/// The eviction policy.
class EvictionPolicy {
 public:
  /// Construct an eviction policy.
  ///
  /// @param store_info Information about the Plasma store that is exposed
  ///        to the eviction policy.
  explicit EvictionPolicy(PlasmaStoreInfo* store_info);

  /// This method will be called whenever an object is first created, in order to
  /// add it to the LRU cache. This is done so when the Plasma store calls
  /// begin_object_access for the first time, we can remove the object from the LRU
  /// cache as usual.
  ///
  /// @param object_id The object ID of the object that was created.
  /// @return Void.
  void object_created(const ObjectID& object_id);

  /// This method will be called when the Plasma store needs more space, perhaps
  /// to create a new object. If the required amount of space cannot be freed up,
  /// then a fatal error will be thrown. Whenever this method is called, the
  /// caller has the burden of handling the objects stored afterwards in 
  /// objects_to_evict. This is because the eviction policy will assume that 
  /// the caller has indeed evicted the objects the policy has chosen to evict from 
  /// the Plasma store.
  ///
  /// @param size The size in bytes of the new object, including both data and
  ///        metadata.
  /// @param objects_to_evict The object IDs that were chosen for eviction will
  ///        be stored into this vector.
  /// @return True if enough space can be freed and false otherwise.
  bool require_space(int64_t size, std::vector<ObjectID>* objects_to_evict);

  /// This method will be called whenever an unused object in the Plasma store
  /// starts to be used. Whenever this method is called, the caller has the burden 
  /// of handling the objects stored afterwards in objects_to_evict. This is because 
  /// the eviction policy will assume that the caller has indeed evicted the objects 
  /// the policy has chosen to evict from the Plasma store.
  ///
  /// @param object_id The ID of the object that is now being used.
  /// @param objects_to_evict The object IDs that were chosen for eviction will
  ///        be stored into this vector.
  /// @return Void.
  void begin_object_access(
      const ObjectID& object_id, std::vector<ObjectID>* objects_to_evict);

  /// This method will be called whenever an object in the Plasma store that was
  /// being used is no longer being used. Whenever this method is called, the
  /// caller has the burden of handling the objects stored afterwards in 
  /// objects_to_evict. This is because the eviction policy will assume that 
  /// the caller has indeed evicted the objects the policy has chosen to evict from 
  /// the Plasma store.
  ///
  /// @param object_id The ID of the object that is no longer being used.
  /// @param objects_to_evict The object IDs that were chosen for eviction will
  ///        be stored into this vector.
  /// @return Void.
  void end_object_access(
      const ObjectID& object_id, std::vector<ObjectID>* objects_to_evict);

  /// Choose some objects to evict from the Plasma store. Whenever this method is 
  /// called, the caller has the burden of handling the objects stored afterwards in 
  /// objects_to_evict. This is because the eviction policy will assume that 
  /// the caller has indeed evicted the objects the policy has chosen to evict from 
  /// the Plasma store.
  ///
  /// @note This method is not part of the API. It is exposed in the header file
  /// only for testing.
  ///
  /// @param num_bytes_required The number of bytes of space to try to free up.
  /// @param objects_to_evict The object IDs that were chosen for eviction will
  ///        be stored into this vector.
  /// @return The total number of bytes of space chosen to be evicted.
  int64_t choose_objects_to_evict(
      int64_t num_bytes_required, std::vector<ObjectID>* objects_to_evict);

 private:
  /// The amount of memory (in bytes) currently being used.
  int64_t memory_used_;
  /// Pointer to the plasma store info.
  PlasmaStoreInfo* store_info_;
  /// Datastructure for the LRU cache.
  LRUCache cache_;
};

} // namespace plasma

#endif  // PLASMA_EVICTION_POLICY_H
