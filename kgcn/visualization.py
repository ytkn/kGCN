import os
import time
import pickle
import collections
from collections import OrderedDict
from pathlib import Path
import string

import joblib
import numpy as np
from rdkit import Chem
from scipy.sparse import coo_matrix
import tensorflow as tf

from kgcn.feed import construct_feed


def sparse_to_dense(sparse):
    """ convert sparse matrix in COOrdinate format to dense matrix.
    Args:
        sparse: sparse matrix in coordinate format
    Returns:
        Return a dense matrix representation of the matrix. The dense matrix is in ndarray format.
    """
    index = sparse[0]  # list(row index, column index)
    data = sparse[1]
    shape = sparse[2]  # matrix size
    return sparse_to_dense_core(index, data, shape)


def sparse_to_dense_core(index, data, shape):
    """ convert sparse matrix in COOrdinate format to dense matrix. (more flexible arguments)
    Args:
        index: list(row index, column index)
        data:
        shape: matrix size
    Returns:
        Return a dense matrix representation of a sparse matrix. The dense matrix is in ndarray format.
    """
    i = index[:, 0]
    j = index[:, 1]
    coo = coo_matrix((data, (i, j)), shape)
    return coo.todense().A  # convert numpy.matrix to ndarray format.


class CompoundVisualizer(object):
    """ Visualize compound information. (this class deals with first degree graph laplacian matrix)
    Attributes:
        outdir (str):
        idx (int): To read dataset with this idx.
        info:
        batch_idx (list[int]):
        placeholders:
        all_data:
        prediction:
        mol: rdkit.Chem
        name (str):
        assay_str (str):
        model:
        logger:
        scaling_target:
    """
    def __init__(self, sess, outdir, idx, info, config, batch_idx, placeholders, all_data, prediction, mol, name, assay_str,
                 target_label, true_label, *, model=None, logger=None, ig_modal_target='all', scaling_target='all'):
        self.logger = _default_logger if logger is None else logger
        self.outdir = outdir
        self.idx = idx
        self.batch_idx = batch_idx
        self.placeholders = placeholders
        self.all_data = all_data
        self.prediction = prediction
        self.target_label = target_label
        self.true_label = true_label
        self.mol = mol
        self.name = name
        self.model = model
        self.assay_str = assay_str
        self.info = info
        self.shapes = OrderedDict()
        self.vector_modal = None
        self.sum_of_ig = 0.0
        self.scaling_target = scaling_target
        self.ig_modal_target = ig_modal_target
        self.config = config

        try:
            self.amino_acid_seq = self.all_data.sequence_symbol[idx]
        except:
            self.logger.info('self.amino_acid_seq is not used.')
            self.logger.info('backward compability')

        if self.scaling_target == 'all':
            self.scaling_target = self._set_ig_modal_target(sess)
        elif self.scaling_target == 'profeat':
            self._set_ig_modal_target(sess)
            self.scaling_target = ['embedded_layer', 'profeat']
        else:
            self._set_ig_modal_target(sess)
            self.scaling_target = [self.scaling_target]

        if self.ig_modal_target == 'all':
            self.ig_modal_target = self.scaling_target
        else:
            self.ig_modal_target = [self.ig_modal_target]

        self.logger.debug(f"self.ig_modal_target = {self.ig_modal_target}")

    def _set_ig_modal_target(self,sess):
        all_ig_modal_target = []
        self.ig_modal_target_data = OrderedDict()

        _features_flag = False
        _adjs_flag = False
        # to skip dragon feature

        if self.all_data.features is not None and "features" in self.placeholders:
            _features_flag = True
            all_ig_modal_target.append('features')
            self.shapes['features'] = (self.info.graph_node_num, self.info.feature_dim)
            self.ig_modal_target_data['features'] = self.all_data.features[self.idx]

        if self.all_data.adjs is not None and "adjs" in self.placeholders:
            _adjs_flag = True
            all_ig_modal_target.append('adjs')
            self.shapes['adjs'] = (self.info.graph_node_num, self.info.graph_node_num)
            self.ig_modal_target_data['adjs'] = sparse_to_dense(self.all_data.adjs[self.idx][0])

        if self.info.vector_modal_name is not None:
            for key, ele in self.info.vector_modal_name.items():
                self.logger.debug(f"for {key}, {ele} in self.info.vector_modal_name.items()")
                if self.all_data['sequences'] is not None and "sequences" in self.placeholders:
                    all_ig_modal_target.append('embedded_layer')
                    _shape = (1, self.info.sequence_max_length, self.info.sequence_symbol_num)
                    self.shapes['embedded_layer'] = _shape
                    _data = self.all_data['sequences']
                    _data = np.expand_dims(_data[self.idx, ...], axis=0)
                    _data = self.model.embedding(sess, _data)
                    self.ig_modal_target_data['embedded_layer'] = _data
                elif key in self.placeholders:
                    all_ig_modal_target.append(key)
                    self.shapes[key] = (1, self.info.vector_modal_dim[ele])
                    self.ig_modal_target_data[key] = self.all_data.vector_modal[ele][self.idx]

        return all_ig_modal_target

    def dump(self, filename=None):
        if filename is None:
            filename = Path(self.outdir) / f"mol_{self.idx:05d}_{self.assay_str}.pkl"
        else:
            filename = Path(self.outdir) / filename
        suffix = filename.suffix
        support_suffixes = ['.pkl', '.npz', '.jbl', '.gml']
        assert suffix in support_suffixes, "You have to choose a filetype in ['pkl', 'npz', 'jbl', '.gml']"
        _out_dict = dict(mol=self.mol)
        try:
            _out_dict['amino_acid_seq'] = ''.join([string.ascii_uppercase[int(i)] for i in self.amino_acid_seq])
        except:
            self.logger.info('self.amino_acid_seq is not used.')
            self.logger.info('backward compatibility')
        #
        for key in self.IGs:
            _out_dict[key] = self.ig_modal_target_data[key]
            _out_dict[key+'_IG'] = self.IGs[key]
        #
        _out_dict["prediction_score"] = self.prediction
        _out_dict["target_label"] = self.target_label
        _out_dict["true_label"] = self.true_label
        _out_dict["check_score"] = self.end_score - self.start_score
        _out_dict["sum_of_IG"] = self.sum_of_ig

        self.logger.info(f"[SAVE] {filename}")
        with open(filename, 'wb') as f:
            if suffix == '.jbl':
                joblib.dump(_out_dict, f)
            elif suffix == '.pkl':
                pickle.dump(_out_dict, f)

    def get_placeholders(self, placeholders):
        _ig_placeholders = []
        for target in self.ig_modal_target:
            if target == "adjs":
                _ig_placeholders.append(placeholders["adjs"][0][0].values)
            else:
                if target in placeholders.keys():
                    _ig_placeholders.append(placeholders[target])
        return _ig_placeholders
    def _construct_feed(self,scaling_coef):
        if 'embedded_layer' not in self.ig_modal_target_data:
            feed_dict = construct_feed(self.batch_idx, self.placeholders, self.all_data, info=self.info, config=self.config,
                                       scaling=scaling_coef, ig_modal_target=self.scaling_target)
        else:
            feed_dict = construct_feed(self.batch_idx, self.placeholders, self.all_data, info=self.info, config=self.config,
                                       scaling=scaling_coef, ig_modal_target=self.scaling_target,
                                       embedded_layer=self.ig_modal_target_data['embedded_layer'] )
        return feed_dict
    def cal_integrated_gradients(self, sess, placeholders, prediction, divide_number):
        """
        Args:
            sess: session object
            placeholders:
            prediction: prediction score（output of the network）
            divide_number: division number of a prediction score
        """
        IGs = OrderedDict()
        for key in self.ig_modal_target:
            self.logger.debug(f'initialize itegrated values: {key} ')
            IGs[key] = np.zeros(self.shapes[key])

        ig_placeholders = self.get_placeholders(placeholders)
        # tf_grads.shape: #ig_placeholders x <input_shape>
        tf_grads = tf.gradients(prediction, ig_placeholders)
        for k in range(divide_number):
            scaling_coef = (k + 1) / float(divide_number)
            feed_dict=self._construct_feed(scaling_coef)
            out_grads = sess.run(tf_grads, feed_dict=feed_dict)
            for idx, modal_name in enumerate(IGs):
                _target_data = self.ig_modal_target_data[modal_name]
                if modal_name is 'adjs':
                    adj_grad = sparse_to_dense_core(self.all_data.adjs[self.idx][0][0], out_grads[idx],
                                                    self.shapes[modal_name])
                    IGs[modal_name] += adj_grad * _target_data / float(divide_number)
                else:
                    IGs[modal_name] += out_grads[idx][0] * _target_data / float(divide_number)
        self.IGs = IGs

        # If IG is calculated correctly, "total of IG" approximately equal to "difference between the prediction score
        # with scaling factor = 1 and with scaling factor = 0".
        self.sum_of_ig = 0
        for values in self.IGs.values():
            self.sum_of_ig += np.sum(values)

    def _get_prediction_score(self, sess, scaling, prediction):
        """ calculate a prediction score corresponding to a scaling factor.
        Args:
            sess: session object
            scaling: scaling factor（0 <= x <= 1）
            batch_idx:
            placeholders:
            all_data:
            prediction: prediction score（output of the network）
        """
        assert 0 <= scaling <= 1, "０以上１以下の実数で指定して下さい。"
        feed_dict = self._construct_feed(scaling)
        return sess.run(prediction, feed_dict=feed_dict)[0]

    def check_IG(self, sess, prediction):
        """ output a file that validate the calculation of IG.
        Args:
            sess: session object
            prediction: prediction score（output of the network）
        """
        self.start_score = self._get_prediction_score(sess, 0, prediction)
        self.end_score = self._get_prediction_score(sess, 1, prediction)

class KnowledgeGraphVisualizer:
    def __init__(self, sess, outdir, info, config, batch_idx, placeholders, all_data, prediction,
                  *, model=None, logger=None):
        self.outdir = outdir
        self.info = info
        self.config = config
        self.batch_idx = batch_idx
        self.logger = logger
        self.placeholders = placeholders
        self.all_data = all_data

        self.prediction = prediction
        self.model = model

        # set `adjs` as visuzalization target
        self.scaling_target = ['embedded_layer',]
        self.ig_modal_target = ['embedded_layer',]
        self.shapes = dict(embedded_layer=(1, # batch_size
                                           info.all_node_num,
                                           config["embedding_dim"]))
        self.ig_modal_target_data = {}
        _data = self.all_data['nodes']
        _data = np.expand_dims(_data[0, ...], axis=0)

        _data = self.model.embedding(sess,_data)
        self.ig_modal_target_data['embedded_layer'] = _data

    def cal_integrated_gradients(self, sess, placeholders, prediction, divide_number):
        """
        Args:
            sess: session object
            placeholders:
            prediction: prediction score（output of the network）
            divide_number: division number of a prediction score
        """
        ig_placeholders = placeholders["embedded_layer"]
        tf_grads = tf.gradients(prediction, ig_placeholders)
        IGs = OrderedDict()

        self.logger.debug(f"self.ig_modal_target = {self.ig_modal_target}")
        for key in self.ig_modal_target:
            self.logger.info(f"{key}: np.zeros({self.shapes[key]})")
            IGs[key] = np.zeros(self.shapes[key], dtype=np.float32)

        for k in range(divide_number):
            s = time.time()
            scaling_coef = (k + 1) / float(divide_number)
            feed_dict = construct_feed(self.batch_idx, self.placeholders, self.all_data,
                                       config=self.config, info=self.info, scaling=scaling_coef,
                                       ig_modal_target=self.scaling_target)
            out_grads = sess.run(tf_grads, feed_dict=feed_dict)
            for idx, modal_name in enumerate(IGs):
                _target_data = self.ig_modal_target_data[modal_name]
                IGs[modal_name] += out_grads[idx][0] * _target_data / float(divide_number)
            self.logger.info(f'[IG] {k:3d}th / {divide_number} : '
                             f'[TIME] {(time.time() - s):7.4f}s')
        self.IGs = IGs

        # If IG is calculated correctly, "total of IG" approximately equal to "difference between the prediction score
        # with scaling factor = 1 and with scaling factor = 0".
        self.sum_of_ig = 0
        for values in self.IGs.values():
            self.sum_of_ig += np.sum(values)

    def dump(self, filename=None, vis_nodes=None):
        self._dump_dml(filename, self.IGs['embedded_layer'], vis_nodes)

    def _dump_dml(self, filename, ig, vis_nodes):
        import networkx as nx
        index, data, shape = self.all_data.adjs[0][0]
        ig = np.squeeze(ig)
        ig = np.sum(ig, axis=-1)
        coo = coo_matrix((data, (index[:, 0], index[:, 1])), shape)
        G = nx.from_scipy_sparse_matrix(coo)

        # set attribute
        norm_ig = (ig - np.mean(ig)) / np.std(ig)
        igs = {idx: _ig for idx, (node, _ig) in enumerate(zip(G.nodes, norm_ig))}
        nx.set_node_attributes(G, igs, 'ig')

        graph_distance = self.config['graph_distance']
        self.logger.info(f'graph_distance = {graph_distance}')
        nodes = set(vis_nodes)
        for k in range(graph_distance):
            _nodes = set()
            for n in nodes:
                _nodes.add(n)
                for m in G.neighbors(n):
                    _nodes.add(m)
            nodes = nodes.union(_nodes)
        H = G.subgraph(list(nodes))
        self.logger.info(f"graph_output = {filename}")

        edgefile = Path(self.outdir) / (filename + '-edge.csv')
        self.logger.info(f'dump {edgefile.resolve()}')
        nx.readwrite.edgelist.write_edgelist(H, edgefile, delimiter=',', data=False)

        nodefile = Path(self.outdir) / (filename + '-node.csv')
        self.logger.info(f'dump {nodefile.resolve()}')
        with nodefile.open('w') as f:
            f.write('label,ig\n')
            for node in H.nodes.data():
                f.write(f'{node[0]},{node[1]["ig"]}\n')


def cal_feature_IG_for_kg(sess, all_data, placeholders, info, config, prediction,
                          model=None, logger=None, verbosity=None, args=None):
    divide_number = 30
    outdir = config["visualize_path"]
    os.makedirs(outdir, exist_ok=True)
    batch_idx = [0,] # assume batch size is only one.
    feed_dict = construct_feed(batch_idx, placeholders, all_data, config=config, batch_size=1, info=info)

    if not 'visualize_target' in config.keys():
        raise ValueError('set "visualize_target" in your config.')
    logger.info(f"config['visualize_target'] = {config['visualize_target']}")

    if config['visualize_target'] is None:
        if 'edge' in config['visualize_type']:
            n_samples = all_data.label_list.shape[1]
        else:
            n_samples = prediction.shape[1]
        logger.info(f'visualization targets are all.')
        logger.info(f'n_samples = {n_samples}')
        targets = range(n_samples)
    else:
        targets = [config['visualize_target'],]

    for target in targets:
        if 'edge' in config['visualize_type']:
            if config['visualize_type'] == 'edge_score':
                _prediction = model.score[target]
            elif config['visualize_type'] == 'edge_loss':
                _prediction = model.loss[target]
            node1 = all_data.label_list[0, target, 0]
            node2 = all_data.label_list[0, target, 1]
            logger.info(f"edge target = {target} => {node1}-{node2}")
            filename = f'edgepred-{node1}-{node2}'
            vis_nodes = [node1, node2]
        else:
            # for node visualzation
            out_prediction = sess.run(prediction, feed_dict=feed_dict)
            target_index = np.argmax(out_prediction[:, target, :])
            _prediction = prediction[:, target, target_index]
            logger.info(f"target_index = {target_index}")
            filename = f'nodepred-{target}'
            vis_nodes = [target,]

        visualizer = KnowledgeGraphVisualizer(outdir, info, config, batch_idx, placeholders, all_data, _prediction,
                                              logger=logger, model=model)
        visualizer.cal_integrated_gradients(sess, placeholders, _prediction, divide_number)
        visualizer.dump(filename, vis_nodes)


def cal_feature_IG(sess, all_data, placeholders, info, config, prediction,
                   ig_modal_target, ig_label_target, *,
                   model=None, logger=None, args=None):
    """ calculate integrated gradients
    Args:
        sess: session object
        all_data:
        placeholders:
        info:
        prediction: prediction score（output of the network）
        ig_modal_target:
        ig_label_target:
        model:
        logger:
        verbosity:
        args:
    """
    divide_number = 100
    header="mol"
    if args is not None and args.visualization_header is not None:
        header=args.visualization_header
    outdir = config["visualize_path"]
    os.makedirs(outdir, exist_ok=True)
    mol_obj_list = info.mol_info["obj_list"] if "mol_info" in info else None

    all_count=0
    correct_count=0
    visualize_ids=range(all_data.num)
    if args.visualize_resample_num:
        visualize_ids=np.random.choice(visualize_ids, args.visualize_resample_num, replace=False)

    for compound_id in visualize_ids:
        s = time.time()
        batch_idx = [compound_id]
        if all_data['sequences'] is not None:
            _data = all_data['sequences']
            _data = np.expand_dims(_data[compound_id, ...], axis=0)
            _data = model.embedding(sess,_data)
            feed_dict = construct_feed(batch_idx, placeholders, all_data, batch_size=1, info=info,embedded_layer=_data)
        else:
            feed_dict = construct_feed(batch_idx, placeholders, all_data, batch_size=1, info=info)

        out_prediction = sess.run(prediction, feed_dict=feed_dict)
        #print("prediction shape",out_prediction.shape)
        # to give consistency with multitask.
        multitask=False
        if len(out_prediction.shape)==1:
            out_prediction = out_prediction[:,np.newaxis,np.newaxis]
        elif len(out_prediction.shape) == 2:
            out_prediction = np.expand_dims(out_prediction, axis=1)
        elif len(out_prediction.shape) == 3:
            if out_prediction.shape[1]>1:
                multitask=True
        # out_prediction: #data x # task x #class
        # labels: data x #task/#label
        for idx in range(out_prediction.shape[1]):
            _out_prediction = out_prediction[0,idx,:]
            if not multitask:
                true_label = np.argmax(all_data.labels[compound_id])
            else:
                true_label = all_data.labels[compound_id,idx]
            # convert a assay string according to a prediction score
            if len(_out_prediction)>2:  # softmax output
                assay_str = "class"+str(np.argmax(_out_prediction))
            elif len(_out_prediction)==2:  # softmax output
                assay_str = "active" if _out_prediction[1] > 0.5 else "inactive"
            else:
                assay_str = "active" if _out_prediction > 0.5 else "inactive"

            if len(prediction.shape) == 3:  # multitask
                _prediction = prediction[:, idx, :]
            else:
                _prediction = prediction

            target_score = 0
            if ig_label_target == "max":
                target_index = np.argmax(_out_prediction)
                target_prediction = _prediction[:, target_index]
                target_score = _out_prediction[target_index]
            elif ig_label_target == "all":
                target_prediction = prediction
                target_index = "all"
                target_score = np.sum(_out_prediction)
            elif ig_label_target == "correct":
                target_index = np.argmax(_out_prediction)
                if not target_index == true_label:
                    continue
                target_prediction = prediction[:, target_index]
                target_score = _out_prediction[target_index]
            elif ig_label_target == "uncorrect":
                target_index = np.argmax(_out_prediction)
                if target_index == true_label:
                    continue
                target_prediction = prediction[:, target_index]
                target_score = _out_prediction[target_index]
            else:
                target_index = int(ig_label_target)
                target_prediction = prediction[:, target_index]
                target_score = _out_prediction[target_index]

            try:
                mol_name = Chem.MolToSmiles(mol_obj_list[compound_id])
                mol_obj = mol_obj_list[compound_id]
            except:
                mol_name = None
                mol_obj = None
            print(f"No.{compound_id}, task={idx}: \"{mol_name}\": {assay_str} (score= {_out_prediction}, "
                  f"true_label= {true_label}, target_label= {target_index}, target_score= {target_score})")

            # --- 各化合物に対応した可視化オブジェクトにより可視化処理を実行
            visualizer = CompoundVisualizer(sess, outdir, compound_id, info, config, batch_idx, placeholders, all_data,
                                            target_score, mol_obj, mol_name, assay_str, target_index, true_label,
                                            logger=logger, model=model, ig_modal_target=ig_modal_target, scaling_target=ig_modal_target)
            visualizer.cal_integrated_gradients(sess, placeholders, target_prediction, divide_number)
            visualizer.check_IG(sess, target_prediction)
            visualizer.dump(f"{header}_{compound_id:04d}_task_{idx}_{assay_str}_{ig_modal_target}_scaling.jbl")
            print(f"prediction score: {target_score}\n"
                  f"check score: {visualizer.end_score - visualizer.start_score}\n"
                  f"sum of IG: {visualizer.sum_of_ig}\n"
                  f"time : {time.time() - s}")
            all_count+=1
            if np.argmax(_out_prediction) == int(true_label):
                correct_count+=1
    print("accuracy(visualized_data)=",correct_count/all_count)

