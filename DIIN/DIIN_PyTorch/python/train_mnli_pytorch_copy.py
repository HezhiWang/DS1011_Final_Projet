import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F
import numpy as np
import os
import importlib
import random
from util import logger
import util.parameters as params
from util.data_processing import *
from util.evaluate_pytorch import *
from tqdm import tqdm
import gzip
import pickle

FIXED_PARAMETERS, config = params.load_parameters()
modname = FIXED_PARAMETERS["model_name"]

if not os.path.exists(FIXED_PARAMETERS["log_path"]):
    os.makedirs(FIXED_PARAMETERS["log_path"])
if not os.path.exists(config.tbpath):
    os.makedirs(config.tbpath)
    config.tbpath = FIXED_PARAMETERS["log_path"]

if config.test:
    logpath = os.path.join(FIXED_PARAMETERS["log_path"], modname) + "_test.log"
else:
    logpath = os.path.join(FIXED_PARAMETERS["log_path"], modname) + ".log"
logger = logger.Logger(logpath)

model = FIXED_PARAMETERS["model_type"]

module = importlib.import_module(".".join(['models', model])) 
MyModel = getattr(module, 'DIIN')

# Logging parameter settings at each launch of training script
# This will help ensure nothing goes awry in reloading a model and we consistenyl use the same hyperparameter settings. 
logger.Log("FIXED_PARAMETERS\n %s" % FIXED_PARAMETERS)


######################### LOAD DATA #############################


if config.debug_model:
    # training_snli, dev_snli, test_snli, training_mnli, dev_matched, dev_mismatched, test_matched, test_mismatched = [],[],[],[],[],[], [], []
    test_matched = load_nli_data(FIXED_PARAMETERS["dev_matched"], shuffle = False)[:499]
    training_snli, dev_snli, test_snli, training_mnli, dev_matched, dev_mismatched, test_mismatched = test_matched, test_matched,test_matched,test_matched,test_matched,test_matched,test_matched
    indices_to_words, word_indices, char_indices, indices_to_chars = sentences_to_padded_index_sequences([test_matched])
    shared_content = load_mnli_shared_content()
else:

    logger.Log("Loading data SNLI")
    training_snli = load_nli_data(FIXED_PARAMETERS["training_snli"], snli=True)
    dev_snli = load_nli_data(FIXED_PARAMETERS["dev_snli"], snli=True)
    test_snli = load_nli_data(FIXED_PARAMETERS["test_snli"], snli=True)

    logger.Log("Loading data MNLI")
    training_mnli = load_nli_data(FIXED_PARAMETERS["training_mnli"])
    dev_matched = load_nli_data(FIXED_PARAMETERS["dev_matched"])
    dev_mismatched = load_nli_data(FIXED_PARAMETERS["dev_mismatched"])

    test_matched = load_nli_data(FIXED_PARAMETERS["test_matched"], shuffle = False)
    test_mismatched = load_nli_data(FIXED_PARAMETERS["test_mismatched"], shuffle = False)

    shared_content = load_mnli_shared_content()

    logger.Log("Loading embeddings")
    indices_to_words, word_indices, char_indices, indices_to_chars = sentences_to_padded_index_sequences([training_mnli, training_snli, dev_matched, dev_mismatched, test_matched, test_mismatched, dev_snli, test_snli])

config.char_vocab_size = len(char_indices.keys())


embedding_dir = os.path.join(config.datapath, "embeddings")
if not os.path.exists(embedding_dir):
    os.makedirs(embedding_dir)


embedding_path = os.path.join(embedding_dir, "mnli_emb_snli_embedding.pkl.gz")

print("embedding path exist")
print(os.path.exists(embedding_path))
if os.path.exists(embedding_path):
    f = gzip.open(embedding_path, 'rb')
    loaded_embeddings = pickle.load(f)
    f.close()
else:
    loaded_embeddings = loadEmbedding_rand(FIXED_PARAMETERS["embedding_data_path"], word_indices)
    f = gzip.open(embedding_path, 'wb')
    pickle.dump(loaded_embeddings, f)
    f.close()

def get_minibatch(dataset, start_index, end_index, training=False):
    indices = range(start_index, end_index)

    genres = [dataset[i]['genre'] for i in indices]
    labels = [dataset[i]['label'] for i in indices]
    pairIDs = np.array([dataset[i]['pairID'] for i in indices])

    premise_pad_crop_pair = hypothesis_pad_crop_pair = [(0,0)] * len(indices)

    premise_vectors = fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence1_binary_parse_index_sequence'][:] for i in indices], premise_pad_crop_pair, 1)
    hypothesis_vectors = fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence2_binary_parse_index_sequence'][:] for i in indices], hypothesis_pad_crop_pair, 1)
    premise_char_vectors = fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence1_binary_parse_char_index'][:] for i in indices], premise_pad_crop_pair, 2, column_size=config.char_in_word_size)
    hypothesis_char_vectors = fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence2_binary_parse_char_index'][:] for i in indices], hypothesis_pad_crop_pair, 2, column_size=config.char_in_word_size)

    premise_pos_vectors = generate_pos_feature_tensor([dataset[i]['sentence1_parse'][:] for i in indices], premise_pad_crop_pair)
    hypothesis_pos_vectors = generate_pos_feature_tensor([dataset[i]['sentence2_parse'][:] for i in indices], hypothesis_pad_crop_pair)

    premise_exact_match = construct_one_hot_feature_tensor([shared_content[pairIDs[i]]["sentence1_token_exact_match_with_s2"][:] for i in range(len(indices))], premise_pad_crop_pair, 1)
    hypothesis_exact_match = construct_one_hot_feature_tensor([shared_content[pairIDs[i]]["sentence2_token_exact_match_with_s1"][:] for i in range(len(indices))], hypothesis_pad_crop_pair, 1)

    premise_exact_match = np.expand_dims(premise_exact_match, 2)
    hypothesis_exact_match = np.expand_dims(hypothesis_exact_match, 2)

    return premise_vectors, hypothesis_vectors, labels, genres, premise_pos_vectors, \
            hypothesis_pos_vectors, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match

def batch_iter(dataset, batch_size):

    start = -1 * batch_size
    dataset_size = len(dataset)
    order = list(range(dataset_size))
    random.shuffle(order)

    while True:
    	start += batch_size
    	genres = []
    	#[dataset[i]['genre'] for i in indices]
    	labels = []
    	#[dataset[i]['label'] for i in indices]
    	pairIDs = []
    	#np.array([dataset[i]['pairID'] for i in indices])
    	premise_pad_crop_pair = hypothesis_pad_crop_pair = [(0,0)] * batch_size
    	premise_vectors = []
    	hypothesis_vectors = []
    	premise_char_vectors = []
    	hypothesis_char_vectors = []

    	premise_pos_vectors = []
    	hypothesis_pos_vectors = []

    	premise_exact_match = []
    	hypothesis_exact_match = []

    	premise_exact_match = []
    	hypothesis_exact_match = []
    	if start > dataset_size - batch_size:
            # Start another epoch.
            start = 0
            random.shuffle(order)

    	batch_indices = order[start:start + batch_size]
    	pairID_s = {}
    	for i in batch_indices:
    		pairID_s[i] = dataset[i]['pairID']
    	#batch = [dataset[index] for index in batch_indices]
    	for i in batch_indices:
        	genres.append(dataset[i]['genre'])
        	labels.append(dataset[i]['label'])
        	pairIDs.append(dataset[i]['pairID'])

        	premise_vectors.append(fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence1_binary_parse_index_sequence'][:]], premise_pad_crop_pair, 1))
        	hypothesis_vectors.append(fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence2_binary_parse_index_sequence'][:]], hypothesis_pad_crop_pair, 1))
        	premise_char_vectors.append(fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence1_binary_parse_char_index'][:]], premise_pad_crop_pair, 2, column_size=config.char_in_word_size))
        	hypothesis_char_vectors.append(fill_feature_vector_with_cropping_or_padding([dataset[i]['sentence2_binary_parse_char_index'][:]], hypothesis_pad_crop_pair, 2, column_size=config.char_in_word_size))

        	premise_pos_vectors.append(generate_pos_feature_tensor([dataset[i]['sentence1_parse'][:]], premise_pad_crop_pair))
        	hypothesis_pos_vectors.append(generate_pos_feature_tensor([dataset[i]['sentence2_parse'][:]], hypothesis_pad_crop_pair))
        	
        	premise_exact_match_ = construct_one_hot_feature_tensor([shared_content[pairID_s[i]]["sentence1_token_exact_match_with_s2"][:]], premise_pad_crop_pair, 1)
        	hypothesis_exact_match_ = construct_one_hot_feature_tensor([shared_content[pairID_s[i]]["sentence2_token_exact_match_with_s1"][:]], hypothesis_pad_crop_pair, 1)

        	premise_exact_match.append(np.expand_dims(premise_exact_match_, 2))
        	hypothesis_exact_match.append(np.expand_dims(hypothesis_exact_match_, 2))

    	yield [premise_vectors, hypothesis_vectors, labels, genres, premise_pos_vectors, \
        	hypothesis_pos_vectors, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
        	premise_exact_match, hypothesis_exact_match]


def train(model, loss_, optim, batch_size, config, train_mnli, train_snli, dev_mat, dev_mismat, dev_snli, data_iter):
    #sess_config = tf.ConfigProto()
    #sess_config.gpu_options.allow_growth=True   
    #self.sess = tf.Session(config=sess_config)
    #self.sess.run(self.init)

    display_epoch_freq = 1
    display_step = config.display_step
    eval_step = config.eval_step
    save_step = config.eval_step
    embedding_dim = FIXED_PARAMETERS["word_embedding_dim"]
    dim = FIXED_PARAMETERS["hidden_embedding_dim"]
    emb_train = FIXED_PARAMETERS["emb_train"]
    keep_rate = FIXED_PARAMETERS["keep_rate"]
    sequence_length = FIXED_PARAMETERS["seq_length"] 
    config = config

    logger.Log("Building model from %s.py" %(model))
    model.train()
    #self.global_step = self.model.global_step

    # tf things: initialize variables and create placeholder for session
    logger.Log("Initializing variables")

    #self.init = tf.global_variables_initializer()
    #self.sess = None
    #self.saver = tf.train.Saver()

    step = 0
    epoch = 0
    best_dev_mat = 0.
    best_mtrain_acc = 0.
    last_train_acc = [.001, .001, .001, .001, .001]
    best_step = 0
    train_dev_set = False
    dont_print_unnecessary_info = False
    collect_failed_sample = False

    # Restore most recent checkpoint if it exists. 
    # Also restore values for best dev-set accuracy and best training-set accuracy
    ckpt_file = os.path.join(FIXED_PARAMETERS["ckpt_path"], modname) + ".ckpt"
    if os.path.isfile(ckpt_file + ".meta"):
        if os.path.isfile(ckpt_file + "_best.meta"):
            #self.saver.restore(self.sess, (ckpt_file + "_best"))
            model.load_state_dict(torch.load(ckpt_file + "_best"))
            completed = False
            dev_acc_mat, dev_cost_mat, confmx = evaluate_classifier(classify, dev_mat, batch_size, completed, model, loss_)
            best_dev_mismat, dev_cost_mismat, _ = evaluate_classifier(classify, dev_mismat, batch_size, completed, model, loss_)
            best_dev_snli, dev_cost_snli, _ = evaluate_classifier(classify, dev_snli, batch_size, completed, model, loss_)
            best_mtrain_acc, mtrain_cost, _ = evaluate_classifier(classify, train_mnli[0:5000], batch_size, completed, model, loss_)
            logger.Log("Confusion Matrix on dev-matched\n{}".format(confmx))
            if alpha != 0.:
                best_strain_acc, strain_cost, _  = evaluate_classifier(classify, train_snli[0:5000], batch_size, completed, model, loss_)
                logger.Log("Restored best matched-dev acc: %f\n Restored best mismatched-dev acc: %f\n Restored best SNLI-dev acc: %f\n Restored best MulitNLI train acc: %f\n Restored best SNLI train acc: %f" %(dev_acc_mat, best_dev_mismat, best_dev_snli,  best_mtrain_acc,  best_strain_acc))
            else:
                logger.Log("Restored best matched-dev acc: %f\n Restored best mismatched-dev acc: %f\n Restored best SNLI-dev acc: %f\n Restored best MulitNLI train acc: %f" %(dev_acc_mat, best_dev_mismat, best_dev_snli, best_mtrain_acc))
            if config.training_completely_on_snli:
                best_dev_mat = best_dev_snli
        else:
            model.load_state_dict(torch.load(ckpt_file))
        logger.Log("Model restored from file: %s" % ckpt_file)

    # Combine MultiNLI and SNLI data. Alpha has a default value of 0, if we want to use SNLI data, it must be passed as an argument.
    beta = int(alpha * len(train_snli))

    ### Training cycle
    logger.Log("Training...")
    logger.Log("Model will use %s percent of SNLI data during training" %(alpha * 100))

    while True:
        """
        if config.training_completely_on_snli:
            training_data = train_snli
            beta = int(alpha * len(train_mnli))
            if config.snli_joint_train_with_mnli:
                training_data = train_snli + random.sample(train_mnli, beta)

        else:
            training_data = train_mnli + random.sample(train_snli, beta)
        random.shuffle(training_data)
        """  
        avg_cost = 0.
        total_batch = int(len(training_data) / batch_size)
        
        # Boolean stating that training has not been completed, 
        completed = False 

        # Loop over all batches in epoch
        for i in range(total_batch):

            # Assemble a minibatch of the next B examples
            minibatch_premise_vectors, minibatch_hypothesis_vectors, minibatch_labels, minibatch_genres, \
            minibatch_pre_pos, minibatch_hyp_pos, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match = next(data_iter)
            
            minibatch_premise_vectors = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_premise_vectors]).squeeze())
            minibatch_hypothesis_vectors = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_hypothesis_vectors]).squeeze())
            #minibatch_genres = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_genres]).squeeze())
            minibatch_pre_pos = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_pre_pos]).squeeze())
            minibatch_hyp_pos = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_hyp_pos]).squeeze())
            #pairIDs = Variable(torch.stack([torch.from_numpy(v) for v in pairIDs]).squeeze())
            premise_char_vectors = Variable(torch.stack([torch.from_numpy(v) for v in premise_char_vectors]).squeeze())
            hypothesis_char_vectors = Variable(torch.stack([torch.from_numpy(v) for v in hypothesis_char_vectors]).squeeze())
            premise_exact_match = Variable(torch.stack([torch.from_numpy(v) for v in premise_exact_match]).squeeze())
            hypothesis_exact_match = Variable(torch.stack([torch.from_numpy(v) for v in hypothesis_exact_match]).squeeze())

            #print(minibatch_labels)
            minibatch_labels = Variable(torch.LongTensor(minibatch_labels))
            #torch.stack([torch.LongTensor(v) for v in minibatch_labels]).squeeze()

            model.zero_grad()
            # Run the optimizer to take a gradient step, and also fetch the value of the 
            # cost function for logging

            output = model(minibatch_premise_vectors, minibatch_hypothesis_vectors, \
                minibatch_pre_pos, minibatch_hyp_pos, premise_char_vectors, hypothesis_char_vectors, \
                premise_exact_match, hypothesis_exact_match)

            lossy = loss_(output, minibatch_labels)
            lossy.backward()
            torch.nn.utils.clip_grad_norm(model.parameters(), config.gradient_clip_value)
            optim.step()

            print(step)
            if step % display_step == 0:
                logger.Log("Step: {} completed".format(step))

            if step % eval_step == 0:
                if config.training_completely_on_snli and dont_print_unnecessary_info:
                    dev_acc_mat = dev_cost_mat = 1.0
                else:
                    dev_acc_mat, dev_cost_mat, confmx = evaluate_classifier(classify, dev_mat, batch_size, completed, model, loss_)
                    logger.Log("Confusion Matrix on dev-matched\n{}".format(confmx))
                
                if config.training_completely_on_snli:
                    dev_acc_snli, dev_cost_snli, _ = evaluate_classifier(classify, dev_snli, batch_size, completed, model, loss_)
                    dev_acc_mismat, dev_cost_mismat = 0,0
                elif not dont_print_unnecessary_info or 100 * (1 - best_dev_mat / dev_acc_mat) > 0.04:
                    dev_acc_mismat, dev_cost_mismat, _ = evaluate_classifier(classify, dev_mismat, batch_size, completed, model, loss_)
                    dev_acc_snli, dev_cost_snli, _ = evaluate_classifier(classify, dev_snli, batch_size, completed, model, loss_)
                else:
                    dev_acc_mismat, dev_cost_mismat, dev_acc_snli, dev_cost_snli = 0,0,0,0

                if dont_print_unnecessary_info and config.training_completely_on_snli:
                    mtrain_acc, mtrain_cost, = 0, 0
                else:
                    mtrain_acc, mtrain_cost, _ = evaluate_classifier(classify, train_mnli[0:5000], batch_size, completed, model, loss_)
                
                if alpha != 0.:
                    if not dont_print_unnecessary_info or 100 * (1 - best_dev_mat / dev_acc_mat) > 0.04:
                        strain_acc, strain_cost,_ = evaluate_classifier(classify, train_snli[0:5000], batch_size, completed, model, loss_)
                    elif config.training_completely_on_snli:
                        strain_acc, strain_cost,_ = evaluate_classifier(classify, train_snli[0:5000], batch_size, completed, model, loss_)
                    else:
                        strain_acc, strain_cost = 0, 0
                    logger.Log("Step: %i\t Dev-matched acc: %f\t Dev-mismatched acc: %f\t Dev-SNLI acc: %f\t MultiNLI train acc: %f\t SNLI train acc: %f" %(step, dev_acc_mat, dev_acc_mismat, dev_acc_snli, mtrain_acc, strain_acc))
                    logger.Log("Step: %i\t Dev-matched cost: %f\t Dev-mismatched cost: %f\t Dev-SNLI cost: %f\t MultiNLI train cost: %f\t SNLI train cost: %f" %(step, dev_cost_mat, dev_cost_mismat, dev_cost_snli, mtrain_cost, strain_cost))
                else:
                    logger.Log("Step: %i\t Dev-matched acc: %f\t Dev-mismatched acc: %f\t Dev-SNLI acc: %f\t MultiNLI train acc: %f" %(step, dev_acc_mat, dev_acc_mismat, dev_acc_snli, mtrain_acc))
                    logger.Log("Step: %i\t Dev-matched cost: %f\t Dev-mismatched cost: %f\t Dev-SNLI cost: %f\t MultiNLI train cost: %f" %(step, dev_cost_mat, dev_cost_mismat, dev_cost_snli, mtrain_cost))

            if step % save_step == 0:
                torch.save(model, ckpt_file)
                if config.training_completely_on_snli:
                    dev_acc_mat = dev_acc_snli
                    mtrain_acc = strain_acc
                best_test = 100 * (1 - best_dev_mat / dev_acc_mat)
                if best_test > 0.04:
                    torch.save(model, ckpt_file + "_best")
                    best_dev_mat = dev_acc_mat
                    best_mtrain_acc = mtrain_acc
                    if alpha != 0.:
                        best_strain_acc = strain_acc
                    best_step = step
                    logger.Log("Checkpointing with new best matched-dev accuracy: %f" %(best_dev_mat))

            if best_dev_mat > 0.777 and not config.training_completely_on_snli:
                eval_step = 500
                save_step = 500

            if best_dev_mat > 0.780 and not config.training_completely_on_snli:
                eval_step = 100
                save_step = 100
                dont_print_unnecessary_info = True 
                if config.use_sgd_at_the_end:
                    optim = torch.optim.SGD(model.parameters(), lr=0.00001)

            if best_dev_mat > 0.872 and config.training_completely_on_snli:
                eval_step = 500
                save_step = 500
            
            if best_dev_mat > 0.878 and config.training_completely_on_snli:
                eval_step = 100
                save_step = 100
                dont_print_unnecessary_info = True 

            step += 1

            # Compute average loss
            avg_cost += lossy / (total_batch * batch_size)
                            
        # Display some statistics about the epoch
        if epoch % display_epoch_freq == 0:
            logger.Log("Epoch: %i\t Avg. Cost: %f" %(epoch+1, avg_cost))
        
        epoch += 1 
        last_train_acc[(epoch % 5) - 1] = mtrain_acc

        # Early stopping
        early_stopping_step = 35000
        progress = 1000 * (sum(last_train_acc)/(5 * min(last_train_acc)) - 1) 


        if (progress < 0.1) or (step > best_step + early_stopping_step):
            logger.Log("Best matched-dev accuracy: %s" %(best_dev_mat))
            logger.Log("MultiNLI Train accuracy: %s" %(best_mtrain_acc))
            if config.training_completely_on_snli:
                train_dev_set = True

                # if dev_cost_snli < strain_cost:
                completed = True
                break
            else:
                completed = True
                break

def classify(examples, completed, batch_size, model, loss_):
    model.eval()
    # This classifies a list of examples
    if (test == True) or (completed == True):
        best_path = os.path.join(FIXED_PARAMETERS["ckpt_path"], modname) + ".ckpt_best"
        model.load_state_dict(torch.load(best_path))
        logger.Log("Model restored from file: %s" % best_path)

    total_batch = int(len(examples) / batch_size)
    pred_size = 3 
    logits = np.empty(pred_size)
    print()
    genres = []
    costs = 0
    
    for i in tqdm(range(total_batch + 1)):
        if i != total_batch:
            minibatch_premise_vectors, minibatch_hypothesis_vectors, minibatch_labels, minibatch_genres, \
            minibatch_pre_pos, minibatch_hyp_pos, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match  = get_minibatch(
                examples, batch_size * i, batch_size * (i + 1))
        else:
            minibatch_premise_vectors, minibatch_hypothesis_vectors, minibatch_labels, minibatch_genres, \
            minibatch_pre_pos, minibatch_hyp_pos, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match = get_minibatch(
                examples, batch_size * i, len(examples))

        minibatch_premise_vectors = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_premise_vectors]).squeeze())
        minibatch_hypothesis_vectors = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_hypothesis_vectors]).squeeze())
        #minibatch_genres = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_genres]).squeeze())
        minibatch_pre_pos = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_pre_pos]).squeeze())
        minibatch_hyp_pos = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_hyp_pos]).squeeze())
        #pairIDs = Variable(torch.stack([torch.from_numpy(v) for v in pairIDs]).squeeze())
        premise_char_vectors = Variable(torch.stack([torch.from_numpy(v) for v in premise_char_vectors]).squeeze())
        hypothesis_char_vectors = Variable(torch.stack([torch.from_numpy(v) for v in hypothesis_char_vectors]).squeeze())
        premise_exact_match = Variable(torch.stack([torch.from_numpy(v) for v in premise_exact_match]).squeeze())
        hypothesis_exact_match = Variable(torch.stack([torch.from_numpy(v) for v in hypothesis_exact_match]).squeeze())
        #print(minibatch_labels)
        minibatch_labels = Variable(torch.LongTensor(minibatch_labels))

        genres += minibatch_genres
        logit = model(minibatch_premise_vectors, minibatch_hypothesis_vectors, \
            minibatch_pre_pos, minibatch_hyp_pos, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match)
        print("Logit size: ", logit.size())
        cost = loss_(logit, minibatch_labels)
        costs += cost
        logits = np.vstack([logits, logit.data.numpy()])

    if test == True:
        logger.Log("Generating Classification error analysis script")
        correct_file = open(os.path.join(FIXED_PARAMETERS["log_path"], "correctly_classified_pairs.txt"), 'w')
        wrong_file = open(os.path.join(FIXED_PARAMETERS["log_path"], "wrongly_classified_pairs.txt"), 'w')

        pred = np.argmax(logits[1:], axis=1)
        LABEL = ["entailment", "neutral", "contradiction"]
        for i in tqdm(range(pred.shape[0])):
            if pred[i] == examples[i]["label"]:
                fh = correct_file
            else:
                fh = wrong_file
            fh.write("S1: {}\n".format(examples[i]["sentence1"].encode('utf-8')))
            fh.write("S2: {}\n".format(examples[i]["sentence2"].encode('utf-8')))
            fh.write("Label:      {}\n".format(examples[i]['gold_label']))
            fh.write("Prediction: {}\n".format(LABEL[pred[i]]))
            fh.write("confidence: \nentailment: {}\nneutral: {}\ncontradiction: {}\n\n".format(logits[1+i, 0], logits[1+i,1], logits[1+i,2]))

        correct_file.close()
        wrong_file.close()
    return genres, np.argmax(logits[1:], axis=1), costs

def generate_predictions_with_id(path, examples, completed, batch_size, model, loss_):
    if (test == True) or (completed == True):
        best_path = os.path.join(FIXED_PARAMETERS["ckpt_path"], modname) + ".ckpt_best"
        model.load_state_dict(torch.load(best_path))
        logger.Log("Model restored from file: %s" % best_path)

    total_batch = int(len(examples) / batch_size)
    pred_size = 3
    logits = np.empty(pred_size)
    costs = 0
    IDs = np.empty(1)
    for i in tqdm(range(total_batch + 1)):
        if i != total_batch:
            minibatch_premise_vectors, minibatch_hypothesis_vectors, minibatch_labels, minibatch_genres, \
            minibatch_pre_pos, minibatch_hyp_pos, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match, premise_inverse_term_frequency, \
            hypothesis_inverse_term_frequency, premise_antonym_feature, hypothesis_antonym_feature, premise_NER_feature, \
            hypothesis_NER_feature  = get_minibatch(
                examples, batch_size * i, batch_size * (i + 1))
        else:
            minibatch_premise_vectors, minibatch_hypothesis_vectors, minibatch_labels, minibatch_genres, \
            minibatch_pre_pos, minibatch_hyp_pos, pairIDs, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match, premise_inverse_term_frequency, \
            hypothesis_inverse_term_frequency, premise_antonym_feature, hypothesis_antonym_feature, premise_NER_feature, \
            hypothesis_NER_feature  = get_minibatch(
                examples, batch_size * i, len(examples))

        minibatch_premise_vectors = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_premise_vectors]).squeeze())
        minibatch_hypothesis_vectors = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_hypothesis_vectors]).squeeze())
        #minibatch_genres = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_genres]).squeeze())
        minibatch_pre_pos = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_pre_pos]).squeeze())
        minibatch_hyp_pos = Variable(torch.stack([torch.from_numpy(v) for v in minibatch_hyp_pos]).squeeze())
        #pairIDs = Variable(torch.stack([torch.from_numpy(v) for v in pairIDs]).squeeze())
        premise_char_vectors = Variable(torch.stack([torch.from_numpy(v) for v in premise_char_vectors]).squeeze())
        hypothesis_char_vectors = Variable(torch.stack([torch.from_numpy(v) for v in hypothesis_char_vectors]).squeeze())
        premise_exact_match = Variable(torch.stack([torch.from_numpy(v) for v in premise_exact_match]).squeeze())
        hypothesis_exact_match = Variable(torch.stack([torch.from_numpy(v) for v in hypothesis_exact_match]).squeeze())
        #print(minibatch_labels)
        minibatch_labels = Variable(torch.LongTensor(minibatch_labels))

        logit = model(minibatch_premise_vectors, minibatch_hypothesis_vectors, \
            minibatch_pre_pos, minibatch_hyp_pos, premise_char_vectors, hypothesis_char_vectors, \
            premise_exact_match, hypothesis_exact_match)
        IDs = np.concatenate([IDs, pairIDs])
        logits = np.vstack([logits, logit])
    IDs = IDs[1:]
    logits = np.argmax(logits[1:], axis=1)
    save_submission(path, IDs, logits)

batch_size = FIXED_PARAMETERS["batch_size"]
completed = False
alpha = FIXED_PARAMETERS["alpha"]


if config.training_completely_on_snli:
    training_data = training_snli
    beta = int(alpha * len(training_mnli))
    if config.snli_joint_train_with_mnli:
        training_data = training_snli + random.sample(training_mnli, beta)

else:
    training_data = training_mnli + random.sample(training_snli, beta)

data_iter = batch_iter(training_data, batch_size)

model = MyModel(config, FIXED_PARAMETERS["seq_length"], emb_dim=FIXED_PARAMETERS["word_embedding_dim"],  hidden_dim=FIXED_PARAMETERS["hidden_embedding_dim"], embeddings=loaded_embeddings, emb_train=FIXED_PARAMETERS["emb_train"])

optim = torch.optim.Adadelta(model.parameters(), lr = FIXED_PARAMETERS["learning_rate"])
loss = nn.CrossEntropyLoss() 

test = params.train_or_test()

if config.preprocess_data_only:
    pass
elif test == False:
    train(model, loss, optim, batch_size, config, training_mnli, training_snli, dev_matched, dev_mismatched, dev_snli, data_iter)
    completed = True
    logger.Log("Acc on matched multiNLI dev-set: %s" %(evaluate_classifier(classify, dev_matched, FIXED_PARAMETERS["batch_size"]))[0], completed, model, loss)
    logger.Log("Acc on mismatched multiNLI dev-set: %s" %(evaluate_classifier(classify, dev_mismatched, FIXED_PARAMETERS["batch_size"]))[0], completed, model, loss)
    logger.Log("Acc on SNLI test-set: %s" %(evaluate_classifier(classify, test_snli, FIXED_PARAMETERS["batch_size"]))[0], completed, model, loss)

    if config.training_completely_on_snli:
        logger.Log("Generating SNLI dev pred")
        dev_snli_path = os.path.join(FIXED_PARAMETERS["log_path"], "snli_dev_{}.csv".format(modname))
        generate_predictions_with_id(dev_snli_path, dev_snli, completed, batch_size, model, loss)

        logger.Log("Generating SNLI test pred")
        test_snli_path = os.path.join(FIXED_PARAMETERS["log_path"], "snli_test_{}.csv".format(modname))
        generate_predictions_with_id(test_snli_path, test_snli, completed, batch_size, model, loss)
        
    else:
        logger.Log("Generating dev matched answers.")
        dev_matched_path = os.path.join(FIXED_PARAMETERS["log_path"], "dev_matched_submission_{}.csv".format(modname))
        generate_predictions_with_id(dev_matched_path, dev_matched, completed, batch_size, model, loss)
        logger.Log("Generating dev mismatched answers.")
        dev_mismatched_path = os.path.join(FIXED_PARAMETERS["log_path"], "dev_mismatched_submission_{}.csv".format(modname))
        generate_predictions_with_id(dev_mismatched_path, dev_mismatched, completed, batch_size, model, loss)

else:
    if config.training_completely_on_snli:
        logger.Log("Generating SNLI dev pred")
        dev_snli_path = os.path.join(FIXED_PARAMETERS["log_path"], "snli_dev_{}.csv".format(modname))
        generate_predictions_with_id(dev_snli_path, dev_snli, completed, batch_size, model, loss)

        logger.Log("Generating SNLI test pred")
        test_snli_path = os.path.join(FIXED_PARAMETERS["log_path"], "snli_test_{}.csv".format(modname))
        generate_predictions_with_id(test_snli_path, test_snli, completed, batch_size, model, loss)
        
    else:
        logger.Log("Evaluating on multiNLI matched dev-set")
        matched_multinli_dev_set_eval = evaluate_classifier(classify, dev_matched, FIXED_PARAMETERS["batch_size"], completed, model, loss)
        logger.Log("Acc on matched multiNLI dev-set: %s" %(matched_multinli_dev_set_eval[0]))
        logger.Log("Confusion Matrix \n{}".format(matched_multinli_dev_set_eval[2]))

        logger.Log("Generating dev matched answers.")
        dev_matched_path = os.path.join(FIXED_PARAMETERS["log_path"], "dev_matched_submission_{}.csv".format(modname))
        generate_predictions_with_id(dev_matched_path, dev_matched, completed, batch_size, model, loss)
        logger.Log("Generating dev mismatched answers.")
        dev_mismatched_path = os.path.join(FIXED_PARAMETERS["log_path"], "dev_mismatched_submission_{}.csv".format(modname))
        generate_predictions_with_id(dev_mismatched_path, dev_mismatched, completed, batch_size, model, loss)