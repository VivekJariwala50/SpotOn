pipeline {
    agent any

    environment {
        APP_NAME = "smart-parking-app"
        DEV_CONTAINER = "smart-parking-dev"
        DEV_PORT = "80"
        APP_PORT = "5055"
        DEV_URL = "http://localhost"
    }

    stages {

        stage('Checkout') {
            steps {
                echo "Checking out code..."
                checkout scm
            }
        }

        stage('Build') {
            steps {
                echo "Building Docker image..."
                sh 'docker build -t $APP_NAME:dev .'
            }
        }

        stage('Test') {
            steps {
                echo "Running tests..."
                // optional: add pytest later
            }
        }

        stage('Deploy') {
    steps {
        echo 'Deploying container...'
        sh '''
        # FORCE CLEAN EVERYTHING
        docker stop $(docker ps -aq) || true
        docker rm -f $(docker ps -aq) || true

        # DOUBLE CHECK PORT
        sudo fuser -k 8000/tcp || true

        # NETWORK
        docker network create smart-network || true

        # START DB
        docker start postgres-db || true

        # RUN APP
        docker run -d \
        --name smart-parking-dev \
        --network smart-network \
        -p 8000:8000 \
        -e DATABASE_URL=postgresql://admin:admin@postgres-db:5432/parking \
        smart-parking-app:dev
        '''
    }
}
    }
    post {
        success {
            echo "========================================"
            echo "Application is live at: $DEV_URL"
            echo "========================================"

            withCredentials([string(credentialsId: 'SLACK_WEBHOOK', variable: 'SLACK_WEBHOOK')]) {
                sh """
                curl -X POST -H 'Content-type: application/json' \
                --data '{\"text\":\"✅ Jenkins Build #${env.BUILD_NUMBER}\\nSmart Parking deployed successfully\\nURL: ${DEV_URL}\"}' \
                "$SLACK_WEBHOOK"
                """
            }
        }

        failure {
            echo "Build failed."

            withCredentials([string(credentialsId: 'SLACK_WEBHOOK', variable: 'SLACK_WEBHOOK')]) {
                sh '''
                curl -X POST -H 'Content-type: application/json' \
                --data "{\"text\":\"❌ Jenkins Build #$BUILD_NUMBER FAILED\nSmart Parking pipeline encountered an error.\"}" \
                "$SLACK_WEBHOOK"
                '''
            }
        }
    }
}
